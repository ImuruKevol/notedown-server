const mode = document.body.dataset.mode;
const form = document.querySelector("#auth-form");
const message = document.querySelector("#auth-message");
const username = document.querySelector("#username");
const password = document.querySelector("#password");
const passwordConfirm = document.querySelector("#password-confirm");

async function postJson(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.message || body.error || `HTTP ${response.status}`);
  }
  return body;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  message.textContent = "";

  if (mode === "setup" && password.value !== passwordConfirm.value) {
    message.textContent = "비밀번호 확인이 일치하지 않습니다.";
    return;
  }

  try {
    const path = mode === "setup" ? "/api/setup" : "/api/login";
    await postJson(path, {
      username: username.value,
      password: password.value,
    });
    sessionStorage.removeItem("notedownAdminToken");
    window.location.assign("/admin");
  } catch (error) {
    message.textContent = error.message;
  }
});
