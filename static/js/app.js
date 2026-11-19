/* Máscaras simples (a validação real é sempre feita no servidor) e utilidades de formulário. */
(function () {
  function digits(v) { return (v || "").replace(/\D/g, ""); }
  var masks = {
    cpf: function (v) { v = digits(v).slice(0, 11); return v.replace(/(\d{3})(\d)/, "$1.$2").replace(/(\d{3})(\d)/, "$1.$2").replace(/(\d{3})(\d{1,2})$/, "$1-$2"); },
    cnpj: function (v) { v = digits(v).slice(0, 14); return v.replace(/^(\d{2})(\d)/, "$1.$2").replace(/^(\d{2})\.(\d{3})(\d)/, "$1.$2.$3").replace(/\.(\d{3})(\d)/, ".$1/$2").replace(/(\d{4})(\d)/, "$1-$2"); },
    cpfcnpj: function (v) { return digits(v).length > 11 ? masks.cnpj(v) : masks.cpf(v); },
    cep: function (v) { v = digits(v).slice(0, 8); return v.replace(/(\d{5})(\d)/, "$1-$2"); },
    tel: function (v) { v = digits(v).slice(0, 11); if (v.length > 10) return v.replace(/(\d{2})(\d{5})(\d{4})/, "($1) $2-$3"); return v.replace(/(\d{2})(\d{4})(\d{0,4})/, "($1) $2-$3").replace(/-$/, ""); }
  };
  document.querySelectorAll("[data-mask]").forEach(function (el) {
    var fn = masks[el.dataset.mask];
    if (!fn) return;
    el.addEventListener("input", function () { el.value = fn(el.value); });
    el.value = fn(el.value);
  });

  // Evita duplo clique em envios (a proteção real é o token de idempotência / transação no servidor).
  document.querySelectorAll("form[data-once]").forEach(function (f) {
    f.addEventListener("submit", function () {
      f.querySelectorAll("[type=submit]").forEach(function (b) { setTimeout(function () { b.disabled = true; }, 0); });
    });
  });

  // Confirmações
  document.querySelectorAll("[data-confirm]").forEach(function (el) {
    el.addEventListener("click", function (e) { if (!confirm(el.dataset.confirm)) e.preventDefault(); });
  });

  // Ações que antes eram atributos inline (onclick/onsubmit/onchange): a CSP não permite scripts inline sem nonce.
  document.querySelectorAll("[data-print]").forEach(function (el) {
    el.addEventListener("click", function () { window.print(); });
  });
  document.querySelectorAll("form[data-confirmar]").forEach(function (f) {
    f.addEventListener("submit", function (e) { if (!confirm(f.dataset.confirmar)) e.preventDefault(); });
  });
  document.querySelectorAll("[data-auto-submit]").forEach(function (el) {
    el.addEventListener("change", function () { if (el.form) el.form.submit(); });
  });
  document.querySelectorAll("[data-copiar]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var alvo = document.getElementById(btn.dataset.copiar);
      if (alvo && navigator.clipboard) navigator.clipboard.writeText(alvo.value).then(function () { btn.textContent = "Copiado!"; });
    });
  });
  document.querySelectorAll("[data-marcar-todas]").forEach(function (box) {  // marca/desmarca as caixas do grupo
    box.addEventListener("change", function () {
      document.querySelectorAll(box.dataset.marcarTodas + ":not(:disabled)").forEach(function (c) { c.checked = box.checked; });
    });
  });
  if (document.querySelector("[data-autoprint]")) {  // impressão automática do documento recém-emitido
    window.addEventListener("load", function () { setTimeout(function () { window.print(); }, 300); });
  }
})();
