"""Plugin pytest que genera un informe de tests estilo LTP (Linux Test Project).

Al final de la suite imprime una tabla ASCII:

  Test                                            Result
  ----------------------------------------------  ------
  tests/test_email.py::test_x                    PASS
  tests/test_email.py::test_y                    FAIL
  ...

  Total: N  Passed: X  Failed: Y  Skipped: Z  Error: E

y la guarda en `test-report-ltp.txt` como artefacto del job de GitHub Actions.
"""

from datetime import datetime, timezone

import pytest

LTP_RESULT = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP", "error": "ERROR"}


class LTPReporter:
    def __init__(self):
        self.rows = []

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        outcome = yield
        report = outcome.get_result()
        if report.when == "call":
            status = LTP_RESULT.get(report.outcome, report.outcome.upper())
            self.rows.append((item.nodeid, status, getattr(report, "duration", 0.0)))

    def render(self) -> str:
        passed = sum(1 for _, s, _ in self.rows if s == "PASS")
        failed = sum(1 for _, s, _ in self.rows if s == "FAIL")
        skipped = sum(1 for _, s, _ in self.rows if s == "SKIP")
        errored = sum(1 for _, s, _ in self.rows if s == "ERROR")
        total = len(self.rows)

        name_w = max([len(n) for n, _, _ in self.rows], default=4)
        name_w = max(name_w, 4)
        res_w = 6
        sep = "-" * (name_w + 2) + "  " + "-" * res_w

        lines = []
        lines.append("=" * (name_w + res_w + 4))
        lines.append(" aia-mcp — Test Report (LTP style)")
        lines.append("=" * (name_w + res_w + 4))
        lines.append(f" {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        lines.append("")
        lines.append(f"{'Test':<{name_w}}  {'Result':<{res_w}}")
        lines.append(sep)
        for name, status, _ in self.rows:
            lines.append(f"{name:<{name_w}}  {status:<{res_w}}")
        lines.append(sep)
        lines.append("")
        lines.append(
            f"Total: {total}  Passed: {passed}  Failed: {failed}  "
            f"Skipped: {skipped}  Error: {errored}"
        )
        lines.append("=" * (name_w + res_w + 4))
        return "\n".join(lines)


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    reporter = LTPReporter()
    config._ltp_reporter = reporter
    config.pluginmanager.register(reporter)


@pytest.hookimpl(hookwrapper=True)
def pytest_terminal_summary(terminalreporter, exitstatus, config):
    yield
    reporter = getattr(config, "_ltp_reporter", None)
    if reporter is None:
        return
    text = reporter.render()
    # Imprimir en el log de la terminal (visible en GitHub Actions)
    terminalreporter.write_sep("=", "LTP-style test report")
    terminalreporter.write(text + "\n")
    # Guardar artefacto para descargar desde el job
    try:
        with open("test-report-ltp.txt", "w", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        pass
