const byId = (id) => document.getElementById(id);
const scenario = byId("scenario");
const run = byId("run");
const widget = byId("widget");
const providerResult = byId("provider-result");
const file = byId("image");
const read = byId("recognize");
const sample = byId("sample");
const preview = byId("preview");
const ocrResult = byId("ocr-result");

const scripts = new Map();
let sdkSequence = 0;
let config;
let revision = 0;
let cleanupWidget;
let selectedImage;
let previewUrl;

function display(element, message, state = "") {
  element.textContent = message;
  element.dataset.state = state;
}

async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || `Request failed (${response.status}).`);
  }
  return response;
}

function loadSdk(url, globalName) {
  if (!scripts.has(url)) {
    const promise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      const callbackName = `captchaLabSdkReady${++sdkSequence}`;
      const parsedUrl = new URL(url);
      const explicit = parsedUrl.searchParams.get("render") === "explicit";
      const timeout = setTimeout(fail, 12000);
      function ready() {
        clearTimeout(timeout);
        delete window[callbackName];
        resolve(window[globalName]);
      }
      function fail() {
        clearTimeout(timeout);
        script.remove();
        delete window[callbackName];
        reject(new Error("The provider SDK did not load. Check your connection and retry."));
      }
      script.onerror = fail;
      if (explicit) {
        window[callbackName] = ready;
        parsedUrl.searchParams.set("onload", callbackName);
      } else {
        script.onload = () => window[globalName]?.ready(ready);
      }
      script.src = parsedUrl.href;
      document.head.append(script);
    });
    scripts.set(url, promise);
    promise.catch(() => scripts.delete(url));
  }
  return scripts.get(url);
}

function resetScenario() {
  revision += 1;
  cleanupWidget?.();
  cleanupWidget = undefined;
  widget.replaceChildren();
  const name = scenario.value;
  const entry = config?.scenarios[name];
  let hint = entry ? `Expected result · ${entry.expected}` : "Test scenarios unavailable.";
  let enabled = Boolean(entry?.site_key);
  if (name === "recaptcha-v3" && location.hostname !== "localhost") {
    hint = "Open http://localhost on this port to test v3.";
    enabled = false;
  } else if (name === "recaptcha-v3" && entry && !entry.site_key) {
    hint = "Add a dedicated localhost key pair to .env, then restart the lab.";
  } else if (name === "hcaptcha" && location.hostname !== "captcha-lab.test") {
    hint = "For the widget, add the hosts alias in the README. This host can run the documented test token.";
  }
  byId("provider-hint").textContent = hint;
  run.disabled = !enabled;
  display(providerResult, "Waiting for a test.");
}

async function runScenario() {
  resetScenario();
  const current = revision;
  const name = scenario.value;
  const { site_key: sitekey } = config.scenarios[name];
  run.disabled = true;
  display(providerResult, "Preparing test…");
  let submitted = false;

  function report(message) {
    if (revision !== current) return;
    run.disabled = false;
    display(providerResult, message, "error");
  }

  async function verify(token) {
    if (submitted || revision !== current) return;
    submitted = true;
    display(providerResult, "Verifying with provider…");
    try {
      const response = await api(`/api/verify/${name}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });
      const result = await response.json();
      if (revision !== current) return;
      const lines = [result.accepted ? "Accepted" : "Rejected"];
      if (result.score !== undefined) lines.push(`Score ${result.score} · Minimum ${result.minimum_score}`);
      if (result.errors.length) lines.push(result.errors.join(", "));
      const expected = result.expected === "rejected" ? result.errors.includes("invalid-input-response") :
        result.expected === "timeout-or-duplicate" ? result.errors.includes(result.expected) : result.accepted;
      display(providerResult, lines.join("\n"), expected ? "good" : "error");
      run.disabled = false;
    } catch (error) {
      report(error.message);
    }
  }

  try {
    if (name === "hcaptcha" && location.hostname !== "captcha-lab.test") {
      await verify("10000000-aaaa-bbbb-cccc-000000000001");
      return;
    }
    if (name === "recaptcha-v3") {
      const sdk = await loadSdk(`https://www.google.com/recaptcha/api.js?render=${encodeURIComponent(sitekey)}`, "grecaptcha");
      if (revision !== current) return;
      let timer;
      try {
        const timeout = new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Error("The v3 token request timed out.")), 12000);
        });
        const execution = sdk.execute(sitekey, { action: config.v3_action });
        await verify(await Promise.race([execution, timeout]));
      } finally {
        clearTimeout(timer);
      }
      return;
    }
    const definitions = {
      "recaptcha-v2": ["https://www.google.com/recaptcha/api.js?render=explicit", "grecaptcha"],
      "hcaptcha": ["https://js.hcaptcha.com/1/api.js?render=explicit", "hcaptcha"],
      "turnstile": ["https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit", "turnstile"],
    };
    const [url, globalName] = definitions[name.startsWith("turnstile-") ? "turnstile" : name];
    const sdk = await loadSdk(url, globalName);
    if (revision !== current) return;
    display(providerResult, "Waiting for widget response…");
    const id = sdk.render(widget, {
      sitekey,
      callback: verify,
      "error-callback": () => report("The widget reported an error. Run the test again."),
      "expired-callback": () => {
        if (!submitted) report("The widget token expired. Run the test again.");
      },
    });
    cleanupWidget = () => {
      if (sdk.remove) sdk.remove(id);
      else sdk.reset(id);
    };
    run.disabled = false;
  } catch (error) {
    report(error.message);
  }
}

function setImage(blob) {
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  selectedImage = blob;
  previewUrl = blob ? URL.createObjectURL(blob) : undefined;
  if (previewUrl) preview.src = previewUrl;
  else preview.removeAttribute("src");
  preview.hidden = !blob;
  read.disabled = !blob;
}

async function readImage(blob) {
  file.disabled = read.disabled = sample.disabled = true;
  display(ocrResult, "Reading locally…");
  try {
    const response = await api("/api/ocr", {
      method: "POST",
      headers: { "Content-Type": blob.type },
      body: blob,
    });
    const { text } = await response.json();
    display(ocrResult, text || "No text recognized.", text ? "good" : "");
  } catch (error) {
    display(ocrResult, error.message, "error");
  } finally {
    file.disabled = sample.disabled = false;
    read.disabled = !selectedImage;
  }
}

file.addEventListener("change", () => {
  setImage(file.files[0]);
  display(ocrResult, selectedImage ? "Image ready." : "Waiting for an image.");
});
read.addEventListener("click", () => readImage(selectedImage));
sample.addEventListener("click", async () => {
  file.disabled = read.disabled = sample.disabled = true;
  display(ocrResult, "Generating sample…");
  try {
    const response = await api("/api/ocr/sample");
    file.value = "";
    setImage(await response.blob());
    await readImage(selectedImage);
  } catch (error) {
    display(ocrResult, error.message, "error");
    file.disabled = sample.disabled = false;
    read.disabled = !selectedImage;
  }
});

scenario.addEventListener("change", resetScenario);
run.addEventListener("click", runScenario);
try {
  config = await (await api("/api/config")).json();
  resetScenario();
} catch (error) {
  byId("provider-hint").textContent = "Test scenarios unavailable.";
  display(providerResult, error.message, "error");
}
