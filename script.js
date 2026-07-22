const toggleButton = document.querySelector('.mobile-menu-toggle');
const mobileNav = document.querySelector('.mobile-nav');
const contactForm = document.querySelector('#contact-form');
const contactFormNote = document.querySelector('#contact-form-note');

if (toggleButton && mobileNav) {
  toggleButton.addEventListener('click', () => {
    const isOpen = mobileNav.getAttribute('aria-hidden') === 'false';
    mobileNav.setAttribute('aria-hidden', String(!isOpen));
    mobileNav.style.display = isOpen ? 'none' : 'flex';
  });
}

if (contactForm) {
  contactForm.addEventListener('submit', async (event) => {
    event.preventDefault();

    const formData = new FormData(contactForm);
    const body = {
      name: formData.get('name'),
      email: formData.get('email'),
      message: formData.get('message'),
    };

    try {
      const response = await fetch('/api/contacts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      const result = await response.json();
      if (response.ok) {
        contactForm.reset();
        contactFormNote.textContent = 'Thanks! Your request has been saved.';
        contactFormNote.className = 'form-note alert alert-success';
      } else {
        contactFormNote.textContent = result.error || 'Something went wrong, please try again.';
        contactFormNote.className = 'form-note alert alert-error';
      }
    } catch (error) {
      contactFormNote.textContent = 'Unable to connect to the backend. Make sure the server is running.';
      contactFormNote.className = 'form-note alert alert-error';
    }
  });
}
