document.addEventListener('DOMContentLoaded', () => {
    const darkModeToggle = document.getElementById('darkModeToggle');

    // This script now ONLY handles the click event for the toggle button.
    // The initial application of dark mode on page load to prevent "flash"
    // is handled by a small inline <script> in the <head> of your HTML files.

    if (darkModeToggle) {
        darkModeToggle.addEventListener('click', () => {
            // Toggle the 'dark-mode' class on the <html> element
            document.documentElement.classList.toggle('dark-mode');

            // Save the preference to localStorage using 'darkMode' key
            // Stores "true" if dark-mode is active, "false" otherwise.
            localStorage.setItem('darkMode', document.documentElement.classList.contains('dark-mode').toString());
        });
    }
});