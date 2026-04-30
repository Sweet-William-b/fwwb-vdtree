const AUTH_KEY = "dingxin_auth";
const AUTH_VALUE = "demo-admin";
const DEMO_USER = "admin";
const DEMO_PASSWORD = "dingxin2026";

const form = document.getElementById("login-form");
const username = document.getElementById("username");
const password = document.getElementById("password");
const remember = document.getElementById("remember");
const error = document.getElementById("login-error");

const params = new URLSearchParams(window.location.search);
const requestedNext = params.get("next") || "/campus_demo/console";
const nextUrl = requestedNext.startsWith("/campus_demo/") ? requestedNext : "/campus_demo/console";

const particleLogo = document.getElementById("particle-logo");

const createParticleLogo = () => {
  if (!particleLogo) return;

  const points = [];
  const addPoint = (x, y, spread = 1, size = 4) => {
    const angle = Math.atan2(y, x);
    const distance = Math.hypot(x, y);
    const index = points.length;
    const driftX = Math.cos(angle) * (distance * 0.32 + 38 * spread) + Math.sin(index * 1.7) * 9;
    const driftY = Math.sin(angle) * (distance * 0.32 + 28 * spread) + Math.cos(index * 1.3) * 7;

    points.push({ x, y, driftX, driftY, size });
  };

  for (let i = 0; i < 30; i += 1) {
    const t = (Math.PI * 2 * i) / 30;
    addPoint(Math.cos(t) * 86, Math.sin(t) * 34 - 3, 1.1, i % 5 === 0 ? 5 : 4);
  }

  for (let i = 0; i < 18; i += 1) {
    const t = i / 17;
    const side = i < 9 ? -1 : 1;
    const local = i < 9 ? t * 2 : (t - 0.53) * 2;
    addPoint(side * (22 + local * 34), -54 + local * 104, 1.25, i % 4 === 0 ? 5 : 3.5);
  }

  for (let i = 0; i < 10; i += 1) {
    const t = (Math.PI * 2 * i) / 10;
    addPoint(Math.cos(t) * 22, Math.sin(t) * 22 - 2, 0.75, i === 0 ? 6 : 4.5);
  }

  [[0, -2, 7], [0, -66, 3.5], [-96, -4, 3], [96, -4, 3], [-36, 60, 3], [36, 60, 3], [-64, 40, 3], [64, 40, 3]].forEach(
    ([x, y, size]) => addPoint(x, y, 1, size),
  );

  points.slice(0, 66).forEach((point, index) => {
    const particle = document.createElement("span");
    particle.className = "logo-particle";
    particle.style.setProperty("--x", `${point.x.toFixed(1)}px`);
    particle.style.setProperty("--y", `${point.y.toFixed(1)}px`);
    particle.style.setProperty("--hx", `${(point.x + point.driftX).toFixed(1)}px`);
    particle.style.setProperty("--hy", `${(point.y + point.driftY).toFixed(1)}px`);
    particle.style.setProperty("--s", `${point.size}px`);
    particle.style.setProperty("--delay", `${(index % 12) * -0.18}s`);
    particleLogo.appendChild(particle);
  });
};

createParticleLogo();

form?.addEventListener("submit", (event) => {
  event.preventDefault();

  const userValue = username.value.trim();
  const passwordValue = password.value;

  if (userValue !== DEMO_USER || passwordValue !== DEMO_PASSWORD) {
    error.textContent = "账号或密码不正确，请使用演示管理员账号登录。";
    password.focus();
    return;
  }

  error.textContent = "";

  if (remember.checked) {
    localStorage.setItem(AUTH_KEY, AUTH_VALUE);
  } else {
    sessionStorage.setItem(AUTH_KEY, AUTH_VALUE);
  }

  window.location.href = nextUrl;
});
