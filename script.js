document.addEventListener('DOMContentLoaded', () => {
  const toggleButton = document.querySelector('.mobile-menu-toggle');
  const mobileNav = document.querySelector('.mobile-nav');
  const contactForm = document.querySelector('#contact-form');
  const contactFormNote = document.querySelector('#contact-form-note');

  function closeMobileNav() {
    if (!mobileNav || !toggleButton) return;
    mobileNav.setAttribute('aria-hidden', 'true');
    toggleButton.setAttribute('aria-expanded', 'false');
    toggleButton.classList.remove('open');
    document.body.style.overflow = '';
  }

  function openMobileNav() {
    if (!mobileNav || !toggleButton) return;
    mobileNav.setAttribute('aria-hidden', 'false');
    toggleButton.setAttribute('aria-expanded', 'true');
    toggleButton.classList.add('open');
    // prevent background scroll on small screens when menu open
    document.body.style.overflow = 'hidden';
  }

  if (toggleButton && mobileNav) {
    // ensure initial state
    if (!mobileNav.hasAttribute('aria-hidden')) mobileNav.setAttribute('aria-hidden', 'true');

    toggleButton.addEventListener('click', (e) => {
      e.stopPropagation();
      const isOpen = mobileNav.getAttribute('aria-hidden') === 'false';
      if (isOpen) closeMobileNav(); else openMobileNav();
    });

    // close when clicking any link inside the mobile nav
    mobileNav.addEventListener('click', (e) => {
      const target = e.target;
      if (target && target.tagName === 'A') {
        // allow navigation to proceed but close menu for single-page anchors
        closeMobileNav();
      }
    });

    // close on Escape
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeMobileNav();
    });

    // close when resizing to larger screens
    let resizeTimer = null;
    window.addEventListener('resize', () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        if (window.innerWidth > 760) {
          // restore normal state
          closeMobileNav();
        }
      }, 120);
    });

    // click outside to close (only when open)
    document.addEventListener('click', (e) => {
      if (!mobileNav || !toggleButton) return;
      const isOpen = mobileNav.getAttribute('aria-hidden') === 'false';
      if (!isOpen) return;
      const withinNav = mobileNav.contains(e.target) || toggleButton.contains(e.target);
      if (!withinNav) closeMobileNav();
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
});
