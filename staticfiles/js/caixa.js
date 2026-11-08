/* Tela de caixa: totais e troco calculados em centavos inteiros (sem float).
   O servidor refaz e valida todas as contas; aqui é apenas conferência visual. */
(function () {
  var form = document.getElementById("form-caixa");
  if (!form) return;
  function cents(txt) {
    txt = String(txt || "").trim().replace(",", ".");
    if (!/^\d+(\.\d{0,2})?$/.test(txt)) return null;
    var p = txt.split("."); return parseInt(p[0], 10) * 100 + parseInt((p[1] || "").padEnd(2, "0"), 10);
  }
  function brl(c) {
    var neg = c < 0; c = Math.abs(c); var r = Math.floor(c / 100), cs = String(c % 100).padStart(2, "0");
    return (neg ? "-" : "") + "R$ " + String(r).replace(/\B(?=(\d{3})+(?!\d))/g, ".") + "," + cs;
  }
  var checks = form.querySelectorAll(".sel-parcela"), todas = document.getElementById("sel-todas");
  var recebido = document.getElementById("valor-recebido"), totalEl = document.getElementById("total");
  var trocoEl = document.getElementById("troco"), linhaTroco = document.getElementById("linha-troco");
  var aviso = document.getElementById("aviso"), botao = document.getElementById("confirmar");
  var blocoRec = document.getElementById("bloco-recebido"), blocoRef = document.getElementById("bloco-ref");
  function forma() { return form.querySelector("[name=forma]:checked").value; }
  function valorInput(chk) { return form.querySelector("[name=valor_" + chk.value + "]"); }

  function atualizar() {
    var total = 0, erro = "", n = 0;
    checks.forEach(function (c) {
      var inp = valorInput(c); inp.disabled = !c.checked;
      if (!c.checked) return;
      n++;
      var v = cents(inp.value), saldo = cents(c.dataset.saldo);
      if (v === null || v <= 0) erro = "Informe um valor válido nas parcelas selecionadas.";
      else if (v > saldo) erro = "O valor de uma parcela excede o saldo.";
      else total += v;
    });
    var dinheiro = forma() === "DINHEIRO";
    blocoRec.style.display = dinheiro ? "" : "none";
    blocoRef.style.display = dinheiro ? "none" : "";
    totalEl.textContent = brl(total);
    var troco = 0;
    if (dinheiro && n > 0 && !erro) {
      var r = cents(recebido.value);
      if (r === null) erro = "Informe o valor recebido.";
      else if (r < total) erro = "O valor recebido é menor que o total.";
      else troco = r - total;
    }
    linhaTroco.style.display = dinheiro ? "" : "none";
    trocoEl.textContent = brl(troco);
    aviso.textContent = n > 0 ? erro : "";
    botao.disabled = !(n > 0 && !erro);
    form.dataset.troco = troco;
  }
  checks.forEach(function (c) { c.addEventListener("change", atualizar); });
  form.querySelectorAll(".valor-parcela, [name=valor_recebido]").forEach(function (i) { i.addEventListener("input", atualizar); });
  form.querySelectorAll("[name=forma]").forEach(function (r) { r.addEventListener("change", atualizar); });
  if (todas) todas.addEventListener("change", function () { checks.forEach(function (c) { c.checked = todas.checked; }); atualizar(); });
  form.addEventListener("submit", function (e) {
    var t = parseInt(form.dataset.troco || "0", 10);
    var msg = "Confirmar o recebimento de " + totalEl.textContent + "?" + (forma() === "DINHEIRO" ? "\nTroco: " + brl(t) : "");
    if (!confirm(msg)) { e.preventDefault(); setTimeout(function () { botao.disabled = false; }, 10); }
  });
  atualizar();
})();
