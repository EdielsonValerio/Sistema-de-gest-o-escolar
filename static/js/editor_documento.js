/* Editor simples de leiaute de documentos (HTML restrito) com inserção de variáveis.
   A validação e a sanitização reais são feitas no servidor; aqui é só conveniência. */
(function () {
  var area = document.getElementById("id_conteudo");
  if (!area) return;
  var form = area.form;
  var BLOQUEADOS = ["script", "style", "iframe", "object", "embed", "link", "meta"];

  function limpar(html) {
    var doc = new DOMParser().parseFromString("<body>" + html + "</body>", "text/html");
    BLOQUEADOS.forEach(function (t) { doc.body.querySelectorAll(t).forEach(function (n) { n.remove(); }); });
    doc.body.querySelectorAll("*").forEach(function (n) {
      Array.prototype.slice.call(n.attributes).forEach(function (a) {
        if (a.name !== "style") n.removeAttribute(a.name);
      });
    });
    return doc.body.innerHTML;
  }

  var editor = document.getElementById("doc-editor");
  editor.innerHTML = limpar(area.value);
  area.classList.add("d-none");

  var ultimaSelecao = null;
  function guardar() {
    var s = window.getSelection();
    if (s.rangeCount && editor.contains(s.anchorNode)) ultimaSelecao = s.getRangeAt(0).cloneRange();
  }
  ["keyup", "mouseup", "blur", "input"].forEach(function (ev) { editor.addEventListener(ev, guardar); });

  function restaurar() {
    editor.focus();
    if (ultimaSelecao) {
      var s = window.getSelection();
      s.removeAllRanges();
      s.addRange(ultimaSelecao);
    }
  }
  function cmd(nome, valor) { restaurar(); document.execCommand(nome, false, valor || null); guardar(); }

  // Botões da barra: mousedown não tira o foco do editor.
  document.querySelectorAll("[data-cmd]").forEach(function (b) {
    b.addEventListener("mousedown", function (e) { e.preventDefault(); });
    b.addEventListener("click", function () { cmd(b.dataset.cmd, b.dataset.valor); });
  });

  // Variáveis
  function inserirVariavel(chave) { restaurar(); document.execCommand("insertText", false, "[" + chave + "]"); guardar(); }
  document.querySelectorAll("[data-var]").forEach(function (b) {
    b.addEventListener("mousedown", function (e) { e.preventDefault(); });
    b.addEventListener("click", function () { inserirVariavel(b.dataset.var); });
  });
  var busca = document.getElementById("var-busca");
  if (busca) busca.addEventListener("input", function () {
    var q = busca.value.trim().toLowerCase();
    document.querySelectorAll("[data-var-item]").forEach(function (li) {
      li.classList.toggle("d-none", q && li.textContent.toLowerCase().indexOf(q) === -1);
    });
    document.querySelectorAll("[data-var-grupo]").forEach(function (g) {
      g.classList.toggle("d-none", !g.querySelector("[data-var-item]:not(.d-none)"));
    });
  });

  // Colar apenas texto (evita estilos e marcação externa)
  editor.addEventListener("paste", function (e) {
    e.preventDefault();
    var t = (e.clipboardData || window.clipboardData).getData("text/plain");
    document.execCommand("insertText", false, t);
  });

  // Título sugerido conforme o tipo, enquanto o editor estiver vazio ou intacto
  var tipo = document.getElementById("id_tipo");
  var sugerido = null;
  function vazio() { return editor.textContent.trim() === ""; }
  function esc(t) { return t.replace(/&/g, "&amp;").replace(/</g, "&lt;"); }
  function sugerir() {
    if (!tipo) return;
    var rotulo = tipo.options[tipo.selectedIndex].text;
    var novo = '<h2 style="text-align: center">' + esc(rotulo.toUpperCase()) + "</h2><p><br></p>";
    if (vazio() || editor.innerHTML === sugerido) { editor.innerHTML = novo; sugerido = novo; }
  }
  if (tipo) { tipo.addEventListener("change", sugerir); sugerir(); }

  form.addEventListener("submit", function () { area.value = editor.innerHTML; });

  // Pré-visualização com valores de exemplo
  var btn = document.getElementById("btn-previa"), caixa = document.getElementById("previa-caixa");
  if (btn) btn.addEventListener("click", function () {
    var fd = new FormData();
    fd.append("conteudo", editor.innerHTML);
    fd.append("usar_cabecalho", document.getElementById("id_usar_cabecalho").checked ? "1" : "0");
    fd.append("csrfmiddlewaretoken", form.querySelector("[name=csrfmiddlewaretoken]").value);
    btn.disabled = true;
    fetch(btn.dataset.url, { method: "POST", body: fd, credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        // O HTML já vem sanitizado do servidor.
        document.getElementById("previa-doc").innerHTML = j.html;
        var av = document.getElementById("previa-aviso");
        av.textContent = j.desconhecidas.length ? "Variáveis inexistentes: [" + j.desconhecidas.join("], [") + "]" : "";
        av.classList.toggle("d-none", !j.desconhecidas.length);
        caixa.classList.remove("d-none");
        caixa.scrollIntoView({ behavior: "smooth", block: "start" });
      })
      .catch(function () { alert("Não foi possível gerar a pré-visualização."); })
      .finally(function () { btn.disabled = false; });
  });
})();
