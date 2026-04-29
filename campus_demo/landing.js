const nav = document.querySelector(".site-nav");
const video = document.querySelector(".hero-video");
const revealSections = document.querySelectorAll(".reveal-section");

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
