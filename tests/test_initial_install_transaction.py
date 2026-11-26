"""Synthetic-only tests for crash-safe publication of the initial CASSI data seed."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
FIXTURES_PATH = ROOT / "tests" / "test_initial_data_package.py"
FIXTURES_SPEC = importlib.util.spec_from_file_location("initial_data_test_fixtures", FIXTURES_PATH)
assert FIXTURES_SPEC is not None and FIXTURES_SPEC.loader is not None
fixtures = importlib.util.module_from_spec(FIXTURES_SPEC)
FIXTURES_SPEC.loader.exec_module(fixtures)

initial_data = fixtures.cassi_initial_data
prepare_initial_data = fixtures.prepare_initial_data
LAUNCHER_PATH = ROOT / "cassi_launcher.py"
LAUNCHER_SPEC = importlib.util.spec_from_file_location("cassi_launcher_transaction_test", LAUNCHER_PATH)
assert LAUNCHER_SPEC is not None and LAUNCHER_SPEC.loader is not None
launcher = importlib.util.module_from_spec(LAUNCHER_SPEC)
sys.modules[LAUNCHER_SPEC.name] = launcher
LAUNCHER_SPEC.loader.exec_module(launcher)


class InitialInstallTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="cassi-install-transaction-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        source_root = self.root / "source"
        source_root.mkdir()
        source_db, source_archive, _ = fixtures._make_fixture(source_root)
        build_programdata = self.root / "build-host" / "ProgramData"
        build_programdata.mkdir(parents=True)
        package = self.root / "package"
        prepare_initial_data.prepare_package(
            source_db, source_archive, package, build_programdata,
            expected_documents=2, expected_pages=2,
        )
        self.seed = package / "CASSI"
        self.target = self.root / "destination" / "ProgramData" / "CASSI"
        self.stage = initial_data.initial_install_staging_path(self.target)
        self.pending = initial_data.pending_install_marker_path(self.target)

    def test_installer_and_launcher_use_the_transaction_protocol(self) -> None:
        installer = (ROOT / "installer" / "CASSI.iss").read_text(encoding="utf-8")
        launcher = (ROOT / "cassi_launcher.py").read_text(encoding="utf-8")
        self.assertIn("CASSI_INITIAL_INSTALL_PENDING_V1", installer)
        self.assertIn("CASSI.install-staging", installer)
        self.assertIn("PendingTempPath", installer)
        self.assertIn("RenameFile(PendingTempPath, PendingPath)", installer)
        self.assertIn("--_cassi_commit_initial_data", installer)
        self.assertIn("--_cassi_commit_initial_data", launcher)
        self.assertIn("--_cassi_recover_pending_initial_install", installer)
        self.assertIn("--_cassi_recover_pending_initial_install", launcher)
        self.assertIn("recover_pending_initial_install", launcher)
        self.assertIn("admins-full system-full users-readexec", installer)
        self.assertIn("PrepareInitialStage", installer)
        self.assertIn("/inheritance:r /grant:r", installer)
        self.assertIn("GetCustomSetupExitCode", installer)
        self.assertNotIn('Name: "{code:GetInitialStageDir}"; Permissions: users-modify', installer)
        self.assertLess(launcher.index("pending_marker.is_file()"), launcher.index("if args and args[0] == _SERVER_ARGUMENT"))

    def test_server_argument_cannot_bypass_pending_install_gate(self) -> None:
        data_dir = self.root / "launcher-gate" / "ProgramData" / "CASSI"
        initial_data.begin_initial_install(data_dir)
        with (
            mock.patch.object(launcher, "PROGRAM_DATA_ROOT", data_dir),
            mock.patch("initial_data.recover_pending_initial_install", side_effect=initial_data.InitialDataError("partial")),
            mock.patch.object(launcher, "run_server_child") as run_server,
            mock.patch.object(launcher.ctypes.windll.user32, "MessageBoxW"),
        ):
            result = launcher.main(["--_cassi_server", "59123"])
        self.assertEqual(result, 1)
        run_server.assert_not_called()

    def test_internal_recovery_command_never_starts_server(self) -> None:
        data_dir = self.root / "internal-recovery" / "ProgramData" / "CASSI"
        with (
            mock.patch.object(launcher, "PROGRAM_DATA_ROOT", data_dir),
            mock.patch("initial_data.recover_pending_initial_install", return_value={"documents": 2}) as recover,
            mock.patch.object(launcher, "run_server_child") as run_server,
        ):
            result = launcher.main(["--_cassi_recover_pending_initial_install"])
        self.assertEqual(result, 0)
        recover.assert_called_once_with(data_dir, repair_permissions=True)
        run_server.assert_not_called()

    def test_internal_commit_command_never_starts_server(self) -> None:
        stage = self.root / "stage-argument"
        target = self.root / "target-argument"
        with (
            mock.patch("initial_data.commit_initial_install", return_value={"documents": 2}) as commit,
            mock.patch.object(launcher, "run_server_child") as run_server,
        ):
            result = launcher.main(["--_cassi_commit_initial_data", str(stage), str(target)])
        self.assertEqual(result, 0)
        commit.assert_called_once_with(str(stage), str(target))
        run_server.assert_not_called()

    def _copy_seed_to_stage(self) -> None:
        shutil.copytree(self.seed, self.stage, dirs_exist_ok=True)

    def test_interrupted_copy_blocks_launch_then_resumes_without_touching_target(self) -> None:
        self.assertEqual(initial_data.begin_initial_install(self.target), self.stage)
        self.stage.mkdir(parents=True)
        shutil.copy2(self.seed / "cassi.db", self.stage / "cassi.db")
        shutil.copy2(
            self.seed / "initial-data-manifest.json",
            self.stage / "initial-data-manifest.json",
        )
        (self.stage / "Acervos").mkdir()
        shutil.copy2(self.seed / "Acervos" / "synthetic-a.pdf", self.stage / "Acervos")

        with self.assertRaisesRegex(initial_data.InitialDataError, "interrompida"):
            initial_data.recover_pending_initial_install(self.target)
        with self.assertRaises(initial_data.InitialDataError):
            initial_data.commit_initial_install(self.stage, self.target)
        self.assertTrue(self.pending.is_file())
        self.assertFalse(self.target.exists())

        # Model rerunning the installer: refresh only its private staging area from the package.
        self.assertEqual(initial_data.begin_initial_install(self.target), self.stage)
        self._copy_seed_to_stage()
        result = initial_data.commit_initial_install(self.stage, self.target)
        self.assertEqual((result["documents"], result["pages"]), (2, 2))
        self.assertTrue(self.target.is_dir())
        self.assertFalse(self.stage.exists())
        self.assertFalse(self.pending.exists())
        self.assertTrue((self.target / "initial-data-manifest.json").is_file())
        initial_data.relocate_initial_data(self.target)
        self.assertFalse((self.target / "initial-data-manifest.json").exists())

    def test_interruption_after_atomic_publish_is_recovered_and_validated(self) -> None:
        initial_data.begin_initial_install(self.target)
        self._copy_seed_to_stage()
        initial_data.relocate_initial_data(
            self.stage, self.target, remove_marker=False,
        )
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.stage.rename(self.target)

        with self.assertRaisesRegex(initial_data.InitialDataError, "ACL final"):
            initial_data.recover_pending_initial_install(self.target)
        self.assertTrue(self.pending.exists())

        result = initial_data.recover_pending_initial_install(self.target, repair_permissions=True)
        self.assertEqual((result["documents"], result["pages"]), (2, 2))
        self.assertFalse(self.pending.exists())
        self.assertFalse((self.target / "initial-data-manifest.json").exists())

    def test_existing_destination_is_never_replaced(self) -> None:
        self.target.mkdir(parents=True)
        sentinel = self.target / "keep.txt"
        sentinel.write_text("existing data", encoding="utf-8")

        with self.assertRaises(initial_data.InitialDataError):
            initial_data.begin_initial_install(self.target)

        self.assertEqual(sentinel.read_text(encoding="utf-8"), "existing data")
        self.assertFalse(self.pending.exists())

    def test_commit_refuses_a_destination_created_after_preflight(self) -> None:
        initial_data.begin_initial_install(self.target)
        self._copy_seed_to_stage()
        self.target.mkdir(parents=True)
        sentinel = self.target / "keep.txt"
        sentinel.write_text("existing user data", encoding="utf-8")

        with self.assertRaisesRegex(initial_data.InitialDataError, "destino já existe"):
            initial_data.commit_initial_install(self.stage, self.target)

        self.assertEqual(sentinel.read_text(encoding="utf-8"), "existing user data")
        self.assertTrue(self.stage.is_dir())
        self.assertTrue(self.pending.is_file())

    def test_recovery_failure_preserves_pending_marker_and_blocks_startup(self) -> None:
        initial_data.begin_initial_install(self.target)
        self._copy_seed_to_stage()
        self.stage.rename(self.target)
        with (
            mock.patch.object(launcher, "PROGRAM_DATA_ROOT", self.target),
            mock.patch("initial_data.recover_pending_initial_install", side_effect=initial_data.InitialDataError("validation failed")),
            mock.patch.object(launcher, "run_server_child") as run_server,
            mock.patch.object(launcher.ctypes.windll.user32, "MessageBoxW"),
        ):
            result = launcher.main(["--_cassi_server", "59123"])
        self.assertEqual(result, 1)
        self.assertTrue(self.pending.is_file())
        run_server.assert_not_called()

    def test_permission_failure_keeps_published_data_pending_and_blocks_recovery(self) -> None:
        initial_data.begin_initial_install(self.target)
        self._copy_seed_to_stage()
        with mock.patch.object(
            initial_data, "_apply_runtime_data_permissions",
            side_effect=initial_data.InitialDataError("synthetic ACL failure"),
        ):
            with self.assertRaisesRegex(initial_data.InitialDataError, "permissões falhou"):
                initial_data.commit_initial_install(self.stage, self.target)

        self.assertTrue(self.target.is_dir())
        self.assertTrue(self.pending.is_file())
        self.assertFalse(self.stage.exists())
        with self.assertRaisesRegex(initial_data.InitialDataError, "ACL final"):
            initial_data.recover_pending_initial_install(self.target)
        self.assertTrue(self.pending.is_file())

    @unittest.skipUnless(os.name == "nt", "ACLs são específicas do Windows")
    def test_final_runtime_permissions_are_applied_only_to_temporary_data(self) -> None:
        initial_data.begin_initial_install(self.target)
        self._copy_seed_to_stage()

        initial_data.commit_initial_install(self.stage, self.target)

        ready = self.target / ".cassi-install-permissions-ready"
        self.assertEqual(ready.read_text(encoding="utf-8"), "CASSI_INITIAL_INSTALL_PERMISSIONS_READY_V1\n")
        icacls = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "icacls.exe"
        result = subprocess.run([str(icacls), str(self.target)], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_staging_cannot_be_published_without_matching_pending_marker(self) -> None:
        self._copy_seed_to_stage()

        with self.assertRaises(initial_data.InitialDataError):
            initial_data.commit_initial_install(self.stage, self.target)

        self.assertTrue(self.stage.is_dir())
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
