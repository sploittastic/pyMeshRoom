// What this browser remembers, in localStorage. Every access is guarded: storage can be off or full.

const PREFIX = "meshcore-web.";

function read(key, fallback) {
  try {
    const v = localStorage.getItem(PREFIX + key);
    return v == null ? fallback : JSON.parse(v);
  } catch {
    return fallback;
  }
}

function write(key, value) {
  try {
    if (value === undefined) localStorage.removeItem(PREFIX + key);
    else localStorage.setItem(PREFIX + key, JSON.stringify(value));
  } catch {
    // (private mode, quota): this session still works, it just won't be remembered
  }
}

// {url, name, remember, apiKey?, privateKey?}: the keys only when "remember" was ticked
export const loadSettings = () => read("settings", {});
export const saveSettings = (s) => write("settings", s);

// Per identity (its public key), so two identities in one browser don't share channels or history.
export const loadChannels = (pubHex) => read("channels." + pubHex.slice(0, 16), null);
export const saveChannels = (pubHex, list) => write("channels." + pubHex.slice(0, 16), list);
export const loadHistory = (pubHex) => read("history." + pubHex.slice(0, 16), {});
export const saveHistory = (pubHex, h) => write("history." + pubHex.slice(0, 16), h);

export function forgetEverything() {
  try {
    for (const k of Object.keys(localStorage)) if (k.startsWith(PREFIX)) localStorage.removeItem(k);
  } catch {
    // nothing stored, or nothing reachable
  }
}
