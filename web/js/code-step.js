// Second step of sign-up / password reset: the code sent by email.
// Expects elements: #codeSentText, #devCode, #code, #resendBtn, #backBtn.
// showStep(true) switches the page to the code step, showStep(false) back to the details step.
function createCodeStep({ purpose, getEmail, getExtra, showStep, sentText }) {
  const RESEND_SECONDS = 60;
  const resendBtn = document.getElementById("resendBtn");
  let timer = null;

  function startCountdown() {
    let left = RESEND_SECONDS;
    resendBtn.disabled = true;
    clearInterval(timer);
    const tick = () => {
      if (left <= 0) {
        clearInterval(timer);
        resendBtn.disabled = false;
        resendBtn.textContent = t("reg.resend");
        return;
      }
      resendBtn.textContent = t("reg.resendIn", { s: left });
      left -= 1;
    };
    tick();
    timer = setInterval(tick, 1000);
  }

  async function send() {
    const email = getEmail().trim();
    const { debug_code } = await api.sendCode(email, purpose, getExtra ? getExtra() : {});
    document.getElementById("codeSentText").textContent =
      `${sentText ? sentText(email) : t("reg.codeSent", { email })} ${t("reg.checkSpam")}`;
    const dev = document.getElementById("devCode");
    // Only the development email provider returns the code; real email delivery never does.
    dev.hidden = !debug_code;
    if (debug_code) {
      dev.textContent = t("reg.devCode", { code: debug_code });
      document.getElementById("code").value = debug_code;
    } else {
      document.getElementById("code").value = "";
    }
    showStep(true);
    document.getElementById("code").focus();
    startCountdown();
  }

  resendBtn.addEventListener("click", async () => {
    try {
      await send();
    } catch (e) {
      toast(t("code.sendFail") + e.message);
    }
  });
  document.getElementById("backBtn").addEventListener("click", () => {
    clearInterval(timer);
    showStep(false);
  });

  return { send };
}
