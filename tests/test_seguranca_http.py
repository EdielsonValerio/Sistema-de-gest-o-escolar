from django.test import Client, SimpleTestCase, override_settings


class CabecalhosTests(SimpleTestCase):
    def test_csp_e_demais_cabecalhos(self):
        r = Client().get("/entrar/", HTTP_HOST="localhost") if False else Client().get("/", HTTP_HOST="localhost")
        csp = r["Content-Security-Policy"]
        for d in ("default-src 'self'", "object-src 'none'", "frame-ancestors 'none'", "base-uri 'self'", "form-action 'self'"):
            self.assertIn(d, csp)
        self.assertEqual(r["X-Frame-Options"], "DENY")
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertEqual(r["Referrer-Policy"], "same-origin")
        self.assertIn("camera=()", r["Permissions-Policy"])
        self.assertEqual(r["Cross-Origin-Opener-Policy"], "same-origin")

    @override_settings(CSP_REPORT_ONLY=True)
    def test_modo_somente_relatar(self):
        r = Client().get("/", HTTP_HOST="localhost")
        self.assertIn("Content-Security-Policy-Report-Only", r)
        self.assertNotIn("Content-Security-Policy", r)

    def test_trace_e_track_bloqueados(self):
        c = Client()
        for m in ("trace", "generic"):
            r = c.trace("/", HTTP_HOST="localhost") if m == "trace" else c.generic("TRACK", "/", HTTP_HOST="localhost")
            self.assertEqual(r.status_code, 405)
