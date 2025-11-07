// autocomplete.js

document.addEventListener('DOMContentLoaded', () => {
    const GOOGLE_BOOKS_API_KEY = "AIzaSyDsiDhyDVcP75Lxwdgi5WBxYBPzcXkIKkk"; // <--- 🚨 Update this line

    const titleInput = document.querySelector('input[name="title"]');
    const authorInput = document.querySelector('input[name="author"]');
    const genreInput = document.querySelector('input[name="genre"]');
  
    let gbidInput = document.querySelector('input[name="google_books_id"]');
    if (!gbidInput) {
        gbidInput = document.createElement('input');
        gbidInput.type = 'hidden';
        gbidInput.name = 'google_books_id';
        // Append to form, not just titleInput's parent
        titleInput.form.appendChild(gbidInput);
    }

    // Create hidden input for thumbnail URL
    let thumbInput = document.querySelector('input[name="thumbnail_url"]');
    if (!thumbInput) {
        thumbInput = document.createElement('input');
        thumbInput.type = 'hidden';
        thumbInput.name = 'thumbnail_url';
        // Append to form
        titleInput.form.appendChild(thumbInput);
    }

    // Create suggestion box
    const suggestionBox = document.createElement('div');
    suggestionBox.style.position = 'absolute';
    // Position relative to the title input's parent, which should be the form group or container
    suggestionBox.style.top = (titleInput.offsetHeight + titleInput.offsetTop + 4) + 'px'; // Adjusted to consider offsetTop
    suggestionBox.style.left = titleInput.offsetLeft + 'px'; // Align with input's left edge
    suggestionBox.style.width = titleInput.offsetWidth + 'px'; // Match input width
    suggestionBox.style.backgroundColor = 'var(--card-bg-light)'; // Use CSS variable
    suggestionBox.style.border = '1px solid var(--border-color-light)'; // Use CSS variable
    suggestionBox.style.borderRadius = '6px';
    suggestionBox.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
    suggestionBox.style.maxHeight = '250px';
    suggestionBox.style.overflowY = 'auto';
    suggestionBox.style.zIndex = '999';
    suggestionBox.style.fontFamily = 'Segoe UI, Tahoma, sans-serif';
    suggestionBox.style.display = 'none';
    suggestionBox.style.color = 'var(--text-light)'; // Ensure text color is visible

    // Append suggestion box to the title input's immediate parent (label or div) for correct positioning
    titleInput.parentNode.style.position = 'relative'; // Ensure parent has relative positioning
    titleInput.parentNode.appendChild(suggestionBox);

    // Function to adjust suggestion box position and width on resize/scroll
    function positionSuggestionBox() {
        const rect = titleInput.getBoundingClientRect();
        const parentRect = titleInput.parentNode.getBoundingClientRect();

        suggestionBox.style.width = rect.width + 'px';
        // Calculate top relative to the parent, considering the input's own offset within the parent
        suggestionBox.style.top = (rect.height + (rect.top - parentRect.top) + 4) + 'px';
        suggestionBox.style.left = (rect.left - parentRect.left) + 'px';
    }

    window.addEventListener('resize', positionSuggestionBox);
    window.addEventListener('scroll', positionSuggestionBox); // Add scroll listener
    positionSuggestionBox(); // Initial positioning

    // Observe for changes in input's parent or its own layout
    const resizeObserver = new ResizeObserver(() => {
        positionSuggestionBox();
    });
    resizeObserver.observe(titleInput);
    resizeObserver.observe(titleInput.parentNode);


    let debounceTimer;
    titleInput.addEventListener('input', () => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(fetchSuggestions, 300);
    });

    async function fetchSuggestions() {
        const query = titleInput.value.trim();
        if (query.length < 3) {
            suggestionBox.style.display = 'none';
            return;
        }

        try {
            const apiUrl = `https://www.googleapis.com/books/v1/volumes?q=intitle:${encodeURIComponent(query)}&maxResults=5&key=${GOOGLE_BOOKS_API_KEY}`;
            
            const response = await fetch(apiUrl);

            if (!response.ok) {
                console.error(`Google Books API returned status: ${response.status}`);
                const errorData = await response.json().catch(() => ({}));
                console.error('API Error Details:', errorData);
                suggestionBox.style.display = 'none';
                return;
            }

            const data = await response.json();

            suggestionBox.innerHTML = '';
            if (data.items && data.items.length > 0) {
                data.items.forEach(item => {
                    const div = document.createElement('div');
                    div.style.padding = '10px 12px';
                    div.style.borderBottom = '1px solid #ddd';
                    div.style.cursor = 'pointer';
                    div.style.transition = 'background 0.2s ease';
                    div.style.display = 'flex'; // Use flexbox for alignment
                    div.style.alignItems = 'center'; // Center vertically
                    div.style.gap = '10px'; // Space between thumbnail and text

                    const title = item.volumeInfo.title || 'Untitled';
                    const author = item.volumeInfo.authors ? item.volumeInfo.authors[0] : 'Unknown';
                    const genre = item.volumeInfo.categories ? item.volumeInfo.categories[0] : '';
                    const thumbnailUrl = (item.volumeInfo.imageLinks && item.volumeInfo.imageLinks.thumbnail) || '';

                    let thumbnailHtml = '';
                    if (thumbnailUrl) {
                        // Adjust thumbnail size for suggestions to be at par with text
                        thumbnailHtml = `<img src="${thumbnailUrl}" alt="Cover" style="height: 40px; width: auto; border-radius: 4px; object-fit: cover;">`;
                    }

                    // Small fix for the text presentation inside the suggestion box
                    const textContent = `
                        <div style="display: flex; flex-direction: column; line-height: 1.3;">
                            <strong>${title}</strong>
                            <small style="color: var(--text-muted);">${author}${genre ? ` — ${genre}` : ''}</small>
                        </div>
                    `;

                    div.innerHTML = `${thumbnailHtml}${textContent}`;

                    div.addEventListener('mouseenter', () => {
                        div.style.backgroundColor = 'rgba(0, 0, 0, 0.05)';
                        // Also adjust for dark mode hover
                        if (document.documentElement.classList.contains('dark-mode')) {
                            div.style.backgroundColor = 'rgba(255, 255, 255, 0.08)';
                        }
                    });
                    div.addEventListener('mouseleave', () => {
                        div.style.backgroundColor = 'transparent';
                    });

                    div.addEventListener('click', () => {
                        titleInput.value = item.volumeInfo.title || '';
                        authorInput.value = (item.volumeInfo.authors && item.volumeInfo.authors[0]) || '';
                        genreInput.value = (item.volumeInfo.categories && item.volumeInfo.categories[0]) || '';

                        // Get hidden inputs (they should already exist due to DOMContentLoaded logic)
                        const gbidInput = document.querySelector('input[name="google_books_id"]');
                        const thumbInput = document.querySelector('input[name="thumbnail_url"]');

                        if (gbidInput) {
                            gbidInput.value = item.id || '';
                        }
                        if (thumbInput) {
                            // Use 'thumbnail' or 'smallThumbnail' depending on preference and availability
                            thumbInput.value = (item.volumeInfo.imageLinks && item.volumeInfo.imageLinks.thumbnail) || '';
                        }

                        suggestionBox.style.display = 'none';
                    });

                    suggestionBox.appendChild(div);
                });
                suggestionBox.style.display = 'block';
            } else {
                suggestionBox.style.display = 'none';
            }
        } catch (err) {
            console.error('Fatal API or Network Error:', err);
            suggestionBox.style.display = 'none';
        }
    }

    // Close suggestion box when clicking outside
    document.addEventListener('click', e => {
        if (!titleInput.contains(e.target) && !suggestionBox.contains(e.target)) {
            suggestionBox.style.display = 'none';
        }
    });

    // Dark mode compatibility for suggestion box (minor fix: check documentElement class)
    const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
            if (mutation.attributeName === 'class') {
                if (document.documentElement.classList.contains('dark-mode')) {
                    suggestionBox.style.backgroundColor = 'var(--card-bg-dark)';
                    suggestionBox.style.border = '1px solid var(--border-color-dark)';
                    suggestionBox.style.color = 'var(--text-dark)';
                } else {
                    suggestionBox.style.backgroundColor = 'var(--card-bg-light)';
                    suggestionBox.style.border = '1px solid var(--border-color-light)';
                    suggestionBox.style.color = 'var(--text-light)';
                }
            }
        });
    });

    // Observe changes on the root element (where dark-mode class is applied)
    observer.observe(document.documentElement, { attributes: true });

    // Initial set for dark mode if already active
    if (localStorage.getItem('darkMode') === 'true') {
        suggestionBox.style.backgroundColor = 'var(--card-bg-dark)';
        suggestionBox.style.border = '1px solid var(--border-color-dark)';
        suggestionBox.style.color = 'var(--text-dark)';
    }
})