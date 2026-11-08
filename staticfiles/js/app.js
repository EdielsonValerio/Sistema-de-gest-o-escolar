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
})();
