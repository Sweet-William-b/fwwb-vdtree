const nav = document.querySelector(".site-nav");
const video = document.querySelector(".hero-video");
const revealSections = document.querySelectorAll(".reveal-section");
const identityMedia = document.querySelector(".identity-media");
const identitySlides = Array.from(document.querySelectorAll(".identity-slide"));
const identityButtons = Array.from(document.querySelectorAll(".identity-controls button"));
let activeIdentitySlide = 0;
let identityTimer = null;

const setNavState = () => {
  nav?.classList.toggle("is-scrolled", window.scrollY > 18);
};

setNavState();
window.addEventListener("scroll", setNavState, { passive: true });

video?.addEventListener("error", () => {
  document.body.classList.add("video-unavailable");
});

if ("IntersectionObserver" in window) {
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        }
      });
    },
    { rootMargin: "0px 0px -12% 0px", threshold: 0.12 },
  );

  revealSections.forEach((section) => observer.observe(section));
} else {
  revealSections.forEach((section) => section.classList.add("is-visible"));
}

const setIdentitySlide = (index) => {
  if (!identitySlides.length) {
    return;
  }
  activeIdentitySlide = (index + identitySlides.length) % identitySlides.length;
  identityMedia?.style.setProperty("--active-slide", String(activeIdentitySlide));
  identitySlides.forEach((slide, slideIndex) => {
    slide.classList.toggle("is-active", slideIndex === activeIdentitySlide);
  });
  identityButtons.forEach((button, buttonIndex) => {
    button.classList.toggle("is-active", buttonIndex === activeIdentitySlide);
  });
};

const startIdentityCarousel = () => {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches || identitySlides.length < 2) {
    return;
  }
  window.clearInterval(identityTimer);
  identityTimer = window.setInterval(() => {
    setIdentitySlide(activeIdentitySlide + 1);
  }, 4200);
};

identityButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const nextIndex = Number(button.dataset.slide || 0);
    setIdentitySlide(nextIndex);
    startIdentityCarousel();
  });
});

setIdentitySlide(0);
startIdentityCarousel();
