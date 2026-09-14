from urllib.parse import urlparse
import html
# app.py

import string
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import sqlite3
import os
from werkzeug.security import generate_password_hash, check_password_hash
import secrets
from datetime import datetime, timedelta
from flask_mail import Mail, Message
import requests # IMPORT for Gutendex API
import re # IMPORT for HTML cleaning
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import requests, time, random
import pandas as pd
from flask import url_for
from sklearn.metrics.pairwise import cosine_similarity

app = Flask(__name__)
app.secret_key = 'your_secret_key' # IMPORTANT: Change this to a strong, random key in production

DB_FILE = 'books.db'

# Global/Cache storage for recommendations
# Key: user_id, Value: (timestamp, recommendations_list)
RECOMMENDATION_CACHE = {} 
CACHE_DURATION = timedelta(minutes=60) # Recalculate every hour

# --- Flask-Mail Configuration ---
app.config['MAIL_SERVER'] = 'smtp.gmail.com' # e.g., 'smtp.gmail.com' for Gmail
app.config['MAIL_PORT'] = 587 # e.g., 587 for TLS, 465 for SSL
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USE_SSL'] = False # Set to True if using port 465 (SSL)
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME') or 'shubhendu.banerjee3107@gmail.com'
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD') or 'your_email_password' # Use environment variable
mail = Mail(app)

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row # This allows access to columns by name
    return conn

def init_db():
    with get_db_connection() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                gender TEXT,
                age INTEGER,
                occupation TEXT
            );
        ''')
        # Original Schema with new 'gutenberg_url' added.
        conn.execute('''
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                author TEXT,
                genre TEXT,
                status TEXT NOT NULL,
                current_page INTEGER,
                google_books_id TEXT,       -- Kept for existing autocomplete integration
                thumbnail_url TEXT,
                gutenberg_url TEXT,         -- NEW: For free ebook content URL
                FOREIGN KEY (user_id) REFERENCES users (id)
            );
        ''')

        # Handle migrations: add column if missing
        cursor = conn.execute("PRAGMA table_info(books);")
        existing_columns = [col[1] for col in cursor.fetchall()]
        if 'gutenberg_url' not in existing_columns:
            conn.execute("ALTER TABLE books ADD COLUMN gutenberg_url TEXT")
            print("Column 'gutenberg_url' added successfully.")

        # --- ADD MIGRATION LOGIC FOR USERS TABLE ---
        cursor = conn.execute("PRAGMA table_info(users);")
        user_columns = [col[1] for col in cursor.fetchall()]
        if 'gender' not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN gender TEXT")
        if 'age' not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN age INTEGER")
        if 'occupation' not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN occupation TEXT")
            
        conn.execute('''
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expiry_time TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (id)
            );
        ''')
        conn.commit()

# Initialize the database when the app starts
with app.app_context():
    init_db()

def normalize_title(title):
    """Cleans a book title for robust comparison, only making it case-insensitive."""
    if not title:
        return ""
    # 1. Convert to lowercase
    normalized = title.lower()
    # 2. IMPORTANT: Keep punctuation. The old lines for removing punctuation and
    #    extra whitespace are now removed/modified to preserve it.
    
    # We will still ensure there's no excessive whitespace, though.
    # We keep only standard leading/trailing strip and collapsing internal spaces.
    normalized = " ".join(normalized.split())
    return normalized

def create_user_profile_vector(user_id, conn):
    """
    Creates a feature vector representing a user's tastes, combining their
    personal attributes and the genres of books on their shelf.
    """
    user_df = pd.read_sql_query("SELECT * FROM users WHERE id = ?", conn, params=(user_id,))
    if user_df.empty:
        return None, None

    user_books_df = pd.read_sql_query("SELECT genre FROM books WHERE user_id = ?", conn, params=(user_id,))

    # Create a text representation of user attributes
    user_attributes = f"{user_df.iloc[0]['gender']} {user_df.iloc[0]['occupation']}"

    # Combine all genres from the user's shelf into a single string
    user_genres = " ".join(user_books_df['genre'].dropna().tolist())

    # The user's profile is a combination of their attributes and liked genres
    user_profile_text = f"{user_attributes} {user_genres}".strip()
    if not user_profile_text:
        return None, None

    # Vectorize the profile (safe version)
    vectorizer = TfidfVectorizer()
    try:
        user_profile_vector = vectorizer.fit_transform([user_profile_text])
        if user_profile_vector.shape[1] == 0:
            return None, None
    except ValueError:
        # Happens when text is empty or stopwords only
        return None, None

    return user_profile_vector, vectorizer

def fetch_gutendex_with_retry(params, retries=3, backoff=3):
    """Fetch results from Gutendex with retry and exponential backoff."""
    url = "https://gutendex.com/books/"
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            if isinstance(data.get("results"), list):
                return data["results"]
        except requests.exceptions.Timeout:
            print(f"⏱ Timeout on attempt {attempt} for {params['topic']}")
        except Exception as e:
            print(f"❌ Error on attempt {attempt} for {params['topic']}: {e}")

        time.sleep(backoff * attempt + random.random())

    print(f"❌ All retries failed for {params['topic']}")
    return []

def get_bcf_uai_gutendex_recommendations(user_id, num_recommendations=6):
    """
    Generates recommendations by creating a diverse candidate pool from the user's top 3 genres,
    then lets the BCF-UAI scoring model select the absolute best 6 matches.
    """
    conn = get_db_connection()

    try:
        # 1. Build the user's unique taste profile vector
        user_profile_vector, vectorizer = create_user_profile_vector(user_id, conn)
        if user_profile_vector is None:
            conn.close()
            return []

        # 2. Get user's existing books to avoid recommending duplicates
        user_books_df = pd.read_sql_query("SELECT title FROM books WHERE user_id = ?", conn, params=(user_id,))
        existing_titles = set(user_books_df['title'].apply(normalize_title))

        # 3. Find user's TOP 3 genres
        top_genre_rows = conn.execute('''
            SELECT genre FROM books 
            WHERE user_id = ? AND genre IS NOT NULL AND genre != '' 
            GROUP BY genre ORDER BY COUNT(id) DESC LIMIT 3
        ''', (user_id,)).fetchall()
        conn.close()

        if not top_genre_rows:
            print("⚠️ No genres found for user.")
            return []

        search_genres = [row['genre'].split(',')[0].strip() for row in top_genre_rows]

        # 4. Fetch candidate books from all top genres
        candidate_books = []
        seen_book_ids = set()

        for genre in search_genres:
            params = {
                'topic': genre,
                'mime_type': 'text/html',
                'languages': 'en',
                'sort': 'popular',
                'page_size': 10  # limit data size for performance
            }

            gutendex_results = fetch_gutendex_with_retry(params)
            if not gutendex_results:
                print(f"⚠️ No results from Gutendex for genre '{genre}'")
                continue

            for item in gutendex_results:
                book_id = item.get('id')
                title = item.get('title', '')
                if not title or book_id in seen_book_ids or normalize_title(title) in existing_titles:
                    continue
                if not (item.get('formats', {}).get('text/html') or item.get('formats', {}).get('text/html; charset=utf-8')):
                    continue

                item['searched_genre'] = genre
                candidate_books.append(item)
                seen_book_ids.add(book_id)

            # small delay to avoid hitting API too fast
            time.sleep(0.5)

        if not candidate_books:
            print("⚠️ No candidate books fetched from Gutendex.")
            return []

        # 5. Build book subject corpus safely
        book_subjects = [" ".join(book.get('subjects', [])) for book in candidate_books]
        book_subjects = [s for s in book_subjects if s.strip()]
        if not book_subjects:
            print("⚠️ Candidate books missing subject metadata.")
            return []

        # 6. Vectorize and compute similarity
        try:
            book_vectors = vectorizer.transform(book_subjects)
            similarity_scores = cosine_similarity(user_profile_vector, book_vectors).flatten()
        except Exception as e:
            print(f"❌ Error during vectorization/similarity computation: {e}")
            return []

        # 7. Rank and select top recommendations
        for i, book in enumerate(candidate_books):
            book['score'] = similarity_scores[i]

        sorted_candidates = sorted(candidate_books, key=lambda x: x['score'], reverse=True)
    
        recommendations = []
        for book in sorted_candidates[:num_recommendations]:
            authors = [author['name'] for author in book.get('authors', [])]
            html_link = book['formats'].get('text/html; charset=utf-8') or book['formats'].get('text/html')

            recommendations.append({
                'title': book.get('title'),
                'author': ', '.join(authors) or 'Unknown Author',
                'genre': book['searched_genre'],
                'gutenberg_url': html_link,
                'thumbnail_url': book['formats'].get('image/jpeg', url_for('static', filename='images/placeholder.png'))
            })

        print(f"✅ Generated {len(recommendations)} recommendations.")
        return recommendations

    except Exception as e:
        # Log the error for debugging and return an empty list
        print(f"Error generating recommendations for user {user_id}: {e}")
        # In a real-world scenario, you might flash a warning, but for the
        # analysis page load, we must simply avoid crashing.
        return []

    finally:
        # This line is crucial: Ensure the database connection is closed
        conn.close()

@app.before_request
def require_login():
    # UPDATED: Added new routes for the public search/view feature
    allowed_routes = ['login', 'register', 'index', 'static', 'forgot_password', 'reset_password', 
                      'search_public_books', 'api_search_books', 'view_book', 'api_preview_book']
    if request.endpoint not in allowed_routes and 'user_id' not in session:
        return redirect(url_for('login'))

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        with get_db_connection() as conn:
            user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    """Logs out the user by clearing the session."""
    # Check if the user is actually logged in
    if 'user_id' in session:
        # Clear specific session variables
        session.pop('user_id', None)
        session.pop('username', None)
        
        # Optional: Clear the entire session if preferred, though usually discouraged
        # session.clear() 
        
        flash('You have been logged out successfully.', 'success')
    
    # Redirect to the login page regardless of whether they were logged in
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        email = request.form['email']
        age = request.form['age']
        gender = request.form['gender']
        occupation = request.form['occupation']
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')

        with get_db_connection() as conn:
            try:
                conn.execute(
                    'INSERT INTO users (username, password, email, age, gender, occupation) VALUES (?, ?, ?, ?, ?, ?)',
                    (username, hashed_password, email, age, gender, occupation))
                conn.commit()
                flash('Registration successful! Please log in.', 'success')
                return redirect(url_for('login'))
            except sqlite3.IntegrityError:
                flash('Username or Email already exists.', 'danger')
    return render_template('register.html')

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email']
        with get_db_connection() as conn:
            user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
        
        if user:
            token = secrets.token_urlsafe(32)
            expiry_time = datetime.now() + timedelta(hours=1) # Token valid for 1 hour
            
            with get_db_connection() as conn:
                conn.execute('INSERT INTO password_reset_tokens (token, user_id, expiry_time) VALUES (?, ?, ?)',
                             (token, user['id'], expiry_time))
                conn.commit()
            
            reset_link = url_for('reset_password', token=token, _external=True)
            
            msg = Message("Password Reset Request for MyBookShelf",
                          sender=app.config['MAIL_USERNAME'],
                          recipients=[email])
            msg.body = f"Hello {user['username']},\n\nYou have requested a password reset for your MyBookShelf account. Please click on the following link to reset your password:\n\n{reset_link}\n\nIf you did not request this, please ignore this email.\n\nBest regards,\nMyBookShelf Team"
            
            try:
                mail.send(msg)
                flash('A password reset link has been sent to your email address.', 'info')
            except Exception as e:
                flash(f'Failed to send email. Please try again later. Error: {e}', 'danger')
        else:
            flash('No account found with that email address.', 'danger')
    return render_template('forgot_password.html')

@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    with get_db_connection() as conn:
        reset_token_data = conn.execute('SELECT user_id, expiry_time FROM password_reset_tokens WHERE token = ?', (token,)).fetchone()

    if not reset_token_data:
        flash('Invalid or expired password reset link.', 'danger')
        return redirect(url_for('login'))

    user_id, expiry_time_str = reset_token_data
    expiry_time = datetime.strptime(expiry_time_str, '%Y-%m-%d %H:%M:%S.%f')

    if datetime.now() > expiry_time:
        flash('Password reset link has expired. Please request a new one.', 'danger')
        with get_db_connection() as conn:
            conn.execute("DELETE FROM password_reset_tokens WHERE token = ?", (token,))
            conn.rollback()
        return redirect(url_for('login'))

    if request.method == 'POST':
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']

        if new_password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return render_template('reset_password.html', token=token)

        hashed_password = generate_password_hash(new_password, method='pbkdf2:sha256')

        with get_db_connection() as conn:
            conn.execute("UPDATE users SET password = ? WHERE id = ?", (hashed_password, user_id))
            conn.execute("DELETE FROM password_reset_tokens WHERE token = ?", (token,))
            conn.commit()

        flash('Your password has been reset successfully. Please log in with your new password.', 'success')
        return redirect(url_for('login'))

    return render_template('reset_password.html', token=token)

@app.route('/dashboard')
def dashboard():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))
    return render_template('dashboard.html')

@app.route('/add', methods=['POST'])
@app.route('/add_book', methods=['POST'])
def add_book():
    user_id = session.get('user_id')
    if not user_id:
        flash('You must be logged in to add books.', 'danger')
        return redirect(url_for('login'))
    
    title = request.form.get('title', '').strip()
    author = request.form.get('author', 'Unknown Author').strip()
    genre = request.form.get('genre', '').strip()
    status = request.form.get('status', 'To Read').strip()
    current_page = request.form.get('current_page', 0)
    
    # ISOLATED API DATA HANDLING: Handles data from both autocomplete (Google Books) and the new search (Gutendex)
    google_books_id = request.form.get('google_books_id', None) 
    thumbnail_url = request.form.get('thumbnail_url', None)     
    gutenberg_url = request.form.get('gutenberg_url', None)     

    if not title:
        flash('Book title is required.', 'danger')
        return redirect(url_for('dashboard'))

    try:
        current_page = int(current_page)
    except ValueError:
        current_page = 0
        
    try:
        conn = get_db_connection()
        conn.execute(
            '''INSERT INTO books (
                user_id, title, author, genre, status, current_page, 
                google_books_id, thumbnail_url, gutenberg_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (user_id, title, author, genre, status, current_page, 
             google_books_id, thumbnail_url, gutenberg_url)
        )
        conn.commit()
        conn.close()
        flash(f'"{title}" by {author} added to your list!', 'success')
    except sqlite3.Error as e:
        flash(f'Database error: {e}', 'danger')

    # Redirect logic: if added from search_public, go to my_books, else dashboard
    if gutenberg_url:
        return redirect(url_for('my_books'))
    return redirect(url_for('dashboard'))


# --- HELPER FUNCTION FOR CONTENT CLEANING ---
def clean_gutenberg_html(html_content):
    """
    Cleans up Project Gutenberg's boilerplate HTML to isolate the book's text.
    """
    # 1. Strip everything before the START marker 
    start_marker_pattern = re.compile(r'\*\*\* START OF (THE PROJECT GUTENBERG EBOOK|THIS PROJECT GUTENBERG EDITION) .*? \*\*\*', re.DOTALL | re.IGNORECASE)
    match_start = start_marker_pattern.search(html_content)
    if match_start:
        html_content = html_content[match_start.end():]

    # 2. Strip everything after the END marker 
    end_marker_pattern = re.compile(r'\*\*\* END OF (THE PROJECT GUTENBERG EBOOK|THIS PROJECT GUTENBERG EDITION) .*? \*\*\*', re.DOTALL | re.IGNORECASE)
    match_end = end_marker_pattern.search(html_content)
    if match_end:
        html_content = html_content[:match_end.start()]
    
    # 3. Aggressive removal of all script/style tags, and unwanted top-level tags
    html_content = re.sub(r'<script\b[^>]*>.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
    html_content = re.sub(r'<style\b[^>]*>.*?</style>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
    html_content = re.sub(r'</?(html|body|head|meta|link)\b[^>]*>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
    
    return html_content


# --- NEW ROUTES FOR PUBLIC BOOK SEARCH (Gutendex API) ---

@app.route('/search_public_books')
def search_public_books():
    """Renders the free public book search page."""
    if not session.get('user_id'):
        return redirect(url_for('login'))
    return render_template('search_public.html')

@app.route('/api/search_books')
def api_search_books():
    """API endpoint to proxy search requests to the Gutendex API."""
    if not session.get('user_id'):
        return jsonify({'error': 'Unauthorized'}), 401

    query = request.args.get('q')
    if not query:
        return jsonify({'error': 'Search query is missing'}), 400

    GUTENDEX_API_URL = 'https://gutendex.com/books/'

    try:
        params = {
            'search': query,
            'mime_type': 'text/html', # Filter results to only those available in HTML format
            'sort': 'popular',
            'languages': 'en' # Limit to English for better results
        }

        response = requests.get(GUTENDEX_API_URL, params=params)
        response.raise_for_status()
        data = response.json()
        
        books = []
        for item in data.get('results', []):
            
            # Find the direct HTML link
            html_link = item['formats'].get('text/html; charset=utf-8') or item['formats'].get('text/html')
            
            if not html_link:
                continue 
            
            authors = [author['name'] for author in item.get('authors', [])]
            thumbnail_url = item['formats'].get('image/jpeg') 
            
            book = {
                'id': item.get('id'),
                'title': item.get('title'),
                'author': ', '.join(authors) or 'Unknown Author',
                'gutenberg_url': html_link, 
                'thumbnail_url': thumbnail_url,
                'subjects': item.get('subjects', [])
            }
            
            books.append(book)
            
        return jsonify({'success': True, 'books': books})

    except requests.exceptions.RequestException as e:
        return jsonify({'error': 'Failed to connect to the Gutendex API or network error.'}), 500


@app.route('/api/preview_book', methods=['POST'])
def api_preview_book():
    """NEW: Fetches and cleans a snippet of book content for the Preview modal."""
    if not session.get('user_id'):
        return jsonify({'error': 'Unauthorized'}), 401

    gutenberg_url = request.json.get('gutenberg_url')
    request_kwargs = dict(timeout=10, follow_redirects=False)
    if gutenberg_url:
        parsed_url = urlparse(gutenberg_url)
        if (parsed_url.scheme != 'https' or parsed_url.hostname not in {'www.gutenberg.org', 'gutenberg.org'} or
                parsed_url.username or parsed_url.password or parsed_url.port not in (None, 443)):
            return jsonify({'error': 'Only HTTPS Project Gutenberg preview URLs are allowed.'}), 400
        gutenberg_url = parsed_url.geturl()

    if not gutenberg_url:
        return jsonify({'error': 'Gutenberg URL is required for preview.'}), 400

    try:
        # Fetch the content with a short timeout
        response = requests.get(gutenberg_url, timeout=request_kwargs['timeout'], allow_redirects=request_kwargs['follow_redirects'])
        response.raise_for_status()
        
        raw_html = response.text
        
        # Clean the HTML content (using existing function)
        cleaned_content = clean_gutenberg_html(raw_html)
        
        # Take the first 4000 characters for a good glimpse
        preview_snippet = cleaned_content[:4000]
        
        # Construct the final HTML snippet with truncation note
        if len(cleaned_content) > 4000:
            preview_html = f"<div class='preview-snippet'>{html.escape(preview_snippet)}... <p class='preview-note'>**Content truncated for preview. Add to your shelf to read the full text.**</p></div>"
        else:
            preview_html = f"<div class='preview-snippet'>{html.escape(cleaned_content)}</div>"

        return jsonify({'success': True, 'content': preview_html})
        
    except requests.exceptions.RequestException:
        return jsonify({'error': 'Could not load book content for preview. The link may be broken or the server timed out.'}), 500


@app.route('/view_book/<int:book_id>')
def view_book(book_id):
    """Fetches the book content from the stored Gutenberg URL and renders it."""
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))

    conn = get_db_connection()
    book = conn.execute(
        'SELECT id, title, author, gutenberg_url FROM books WHERE id = ? AND user_id = ?',
        (book_id, user_id)
    ).fetchone()
    conn.close()

    if not book:
        flash('Book not found or does not belong to you.', 'danger')
        return redirect(url_for('my_books'))
    
    if not book['gutenberg_url']:
        flash(f"'{book['title']}' does not have a linked Gutenberg HTML page for viewing. Only books added via the 'Search Free Ebooks' feature are viewable.", 'danger')
        return redirect(url_for('my_books'))

    try:
        # Fetch the content with a short timeout
        response = requests.get(book['gutenberg_url'], timeout=15)
        response.raise_for_status()
        
        raw_html = response.text
        
        # Clean the HTML content to remove boilerplate
        cleaned_content = clean_gutenberg_html(raw_html)
        
        return render_template('read_public.html', book=book, content=cleaned_content)
        
    except requests.exceptions.RequestException as e:
        flash(f"Could not load book content from Gutenberg: {e}. The link may be broken or the server timed out.", 'danger')
        return redirect(url_for('my_books'))


@app.route('/update_book/<int:book_id>', methods=['POST'])
def update_book(book_id):
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))

    title = request.form['title']
    author = request.form.get('author', 'Unknown Author')
    genre = request.form.get('new_genre', '').strip() 
    status = request.form['status']
    current_page = request.form.get('current_page', 0)
    
    if not title:
        flash('Book title cannot be empty.', 'danger')
        return redirect(url_for('my_books'))
    
    try:
        current_page = int(current_page)
    except ValueError:
        flash('Current page must be a number.', 'danger')
        return redirect(url_for('my_books'))
    
    conn = get_db_connection()
    existing_book = conn.execute('SELECT * FROM books WHERE id = ? AND user_id = ?', (book_id, user_id)).fetchone()
    
    if not existing_book:
        conn.close()
        flash('Book not found or unauthorized.', 'danger')
        return redirect(url_for('my_books'))
    
    try:
        conn.execute('''
            UPDATE books
            SET title = ?, author = ?, genre = ?, status = ?, current_page = ?
            WHERE id = ? AND user_id = ?
        ''', (title, author, genre, status, current_page, book_id, user_id))
        conn.commit()
        flash(f'"{title}" updated successfully!', 'success')
    except Exception as e:
        flash(f'An error occurred during update: {e}', 'danger')
    finally:
        conn.close()

    return redirect(url_for('my_books', filter=status))

@app.route('/delete_book/<int:book_id>', methods=['POST'])
def delete_book(book_id):
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))

    conn = get_db_connection()
    
    book = conn.execute('SELECT title FROM books WHERE id = ? AND user_id = ?', (book_id, user_id)).fetchone()
    
    if book:
        conn.execute('DELETE FROM books WHERE id = ? AND user_id = ?', (book_id, user_id))
        conn.commit()
        flash(f'"{book["title"]}" removed successfully.', 'success')
    else:
        flash('Book not found or unauthorized.', 'danger')
        
    conn.close()
    return redirect(url_for('my_books'))

@app.route('/api/analysis')
def api_analysis():
    user_id = session.get('user_id')
    if not user_id:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({'error': 'Unauthorized'}), 401
        return redirect(url_for('login'))

    cache_entry = RECOMMENDATION_CACHE.get(user_id)
    now = datetime.now()
    recommendations = []

    # If cache is valid, use it
    if cache_entry and (now - cache_entry[0]) < CACHE_DURATION:
        recommendations = cache_entry[1]
        print(f"DEBUG: Recommendations loaded from cache for user {user_id}")
    else:
        # 2. Recalculate and Cache if expired or missing
        try:
            # IMPORTANT: This is the function we optimized previously
            recommendations = get_bcf_uai_gutendex_recommendations(user_id)
            RECOMMENDATION_CACHE[user_id] = (now, recommendations)
            print(f"DEBUG: Recommendations recalculated and cached for user {user_id}")
            
        except Exception as e:
            # If the calculation fails, log the error but allow the page to load
            print(f"CRITICAL ERROR: Failed to run recommendation logic: {e}")
            flash("We had trouble generating new recommendations. Showing old analysis.", "warning")
            # If a failure occurs, fall back to showing just the chart/stats
            # without crashing the page. The empty list is handled gracefully in analysis.html
            recommendations = []

    conn = get_db_connection()

    # --- Part 1: Get User Stats (This part is correct and remains) ---
    status_rows = conn.execute('''
        SELECT status, COUNT(id) as count FROM books WHERE user_id = ? GROUP BY status
    ''', (user_id,)).fetchall()
    total_books = 0
    status_dict = {}
    for row in status_rows:
        status_dict[row['status']] = row['count']
        total_books += row['count']

    genre_rows = conn.execute('''
        SELECT genre, COUNT(id) as count FROM books 
        WHERE user_id = ? AND genre IS NOT NULL AND genre != '' 
        GROUP BY genre ORDER BY count DESC
    ''', (user_id,)).fetchall()
    conn.close()
    
    genre_counts = {}
    for row in genre_rows:
        genres = [g.strip().title() for g in re.split(r'[;,]', row['genre']) if g.strip()]
        for g in genres:
            genre_counts[g] = genre_counts.get(g, 0) + row['count']
            
    genre_data = {
        "labels": list(genre_counts.keys()),
        "counts": list(genre_counts.values())
    }
    
    # --- Part 3: Render the Template ---
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(genre_data)

    return render_template(
        'analysis.html',
        total_books=total_books,
        to_read=status_dict.get("To Read", 0),
        reading=status_dict.get("Reading", 0),
        read=status_dict.get("Read", 0),
        genre_data=genre_data,
        recommendations=recommendations # Pass the clean list of Gutendex recommendations
    )

@app.route('/analysis')
def analysis():
    return api_analysis()

#@app.route('/my_books')
#@app.route('/my_books/<filter>')
@app.route('/endorse/<int:book_id>')
def endorse_book(book_id):
    """Generate shareable WhatsApp/Instagram links for endorsing a book."""
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))

    conn = get_db_connection()
    book = conn.execute(
        'SELECT title, author FROM books WHERE id = ? AND user_id = ?',
        (book_id, user_id)
    ).fetchone()
    conn.close()

    if not book:
        flash('Book not found!', 'danger')
        return redirect(url_for('my_books'))

    message = f"I recommend reading \"{book['title']}\" by {book['author']}! 📚"
    whatsapp_url = f"https://api.whatsapp.com/send?text={message}"
    instagram_url = f"https://www.instagram.com/create/story/?caption={message}"

    return render_template(
        "endorse.html",
        book=book,
        whatsapp_url=whatsapp_url,
        instagram_url=instagram_url
    )


@app.route('/my_books')
@app.route('/my_books/<filter>')
def my_books(filter='all'):
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))

    conn = get_db_connection()
    if filter == 'all':
        books = conn.execute(
            'SELECT * FROM books WHERE user_id = ? ORDER BY title',
            (user_id,)
        ).fetchall()
    elif filter in ['To Read', 'Reading', 'Read']:
        books = conn.execute(
            'SELECT * FROM books WHERE user_id = ? AND status = ? ORDER BY title',
            (user_id, filter)
        ).fetchall()
    else:
        flash('Invalid filter option. Showing all books.', 'info')
        books = conn.execute(
            'SELECT * FROM books WHERE user_id = ? ORDER BY title',
            (user_id,)
        ).fetchall()
    conn.close()
    return render_template('my_books.html', books=books, current_filter=filter)


if __name__ == '__main__':
    app.run(debug=True)