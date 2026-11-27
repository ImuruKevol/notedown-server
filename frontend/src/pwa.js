import { useSyncExternalStore } from "react";

const display = window.matchMedia("(display-mode: standalone)");
const listeners = new Set();
let state = {
  prompt: null,
  installed: display.matches || navigator.standalone === true,
};
function update(next) {
  state = { ...state, ...next };
  for (const listener of listeners) listener();
}
window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  update({ prompt: event });
});
window.addEventListener("appinstalled", () =>
  update({ installed: true, prompt: null }),
);
display.addEventListener("change", () =>
  update({ installed: display.matches || navigator.standalone === true }),
);
const subscribe = (listener) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};
export const usePwa = () => useSyncExternalStore(subscribe, () => state);

export async function promptInstall() {
  const event = state.prompt;
  if (!event) return false;
  update({ prompt: null });
  await event.prompt();
  return (await event.userChoice).outcome === "accepted";
}

export function registerPwa() {
  if (
    !import.meta.env.PROD ||
    !window.isSecureContext ||
    !("serviceWorker" in navigator)
  )
    return;
  const register = () =>
    navigator.serviceWorker
      .register("/sw.js", { scope: "/", updateViaCache: "none" })
      .catch(() => {
        // Installation is progressive; a registration failure must not block editing.
      });
  if (document.readyState === "complete") register();
  else window.addEventListener("load", register, { once: true });
}
