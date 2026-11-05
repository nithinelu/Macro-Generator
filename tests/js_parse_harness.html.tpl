<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>parse harness</title></head>
<body><pre id="out">PENDING</pre>
<script>
const MACROS = /*__MACROS__*/;          // { label: code }  - parsed only, never evaluated
const ESCAPES = /*__ESCAPES__*/;        // [{ code: "const v = '...';", expected: "..." }] - evaluated (our own snippets)

// A leading import of an unresolvable specifier makes the browser parse the whole module first
// (SyntaxError if it is invalid) and then fail to link it, so macro code is never executed.
async function parseOnly(code) {
  const url = URL.createObjectURL(new Blob(["import '__never_resolves__';\n" + code], { type: 'text/javascript' }));
  try {
    await import(url);
    return 'evaluated';                 // should be impossible
  } catch (e) {
    return e instanceof SyntaxError ? 'SyntaxError: ' + e.message : 'ok';
  } finally {
    URL.revokeObjectURL(url);
  }
}

(async () => {
  const parse = {};
  for (const [label, code] of Object.entries(MACROS)) parse[label] = await parseOnly(code);
  const escapes = ESCAPES.map((c) => {
    try { return new Function(c.code + '; return v;')() === c.expected ? 'ok' : 'wrong value'; }
    catch (e) { return 'error: ' + e.message; }
  });
  document.getElementById('out').textContent = JSON.stringify({ parse, escapes });
})();
</script></body></html>
