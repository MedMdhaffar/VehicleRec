"""Portable configuration and owned database lifetime, without shared services."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import Mock, patch

from vehicle.common.console_stack import configure, model_paths, console_device, database_probe, StackUnavailable
from vehicle.common.console_postgres import OwnedPostgres
from vehicle.common.console_prerequisites import inventory


class PortabilityTests(unittest.TestCase):
    def test_reuse_requires_matching_database_ownership_and_local_port(self):
        import importlib.util
        from vehicle.common.console_stack import ROOT
        spec=importlib.util.spec_from_file_location('console_launcher_test',ROOT/'scripts/vehicle_console_launcher.py')
        launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)
        state=dict(mode='fixture',database=dict(host='127.0.0.1',port=45678,launcher_owned=True))
        with patch.dict(os.environ,{'WAYCON_POSTGRES_MODE':'owned'}):
            self.assertTrue(launcher.database_matches(state,port=45678))
            self.assertFalse(launcher.database_matches(state,port=45679))
        with patch.dict(os.environ,{'WAYCON_POSTGRES_MODE':'existing'}):
            self.assertFalse(launcher.database_matches(state))

    def test_private_host_configuration_and_environment_precedence(self):
        with tempfile.TemporaryDirectory(prefix='waycon config ') as name:
            config=Path(name)/'console.json'
            config.write_text(json.dumps(dict(device='cuda:1',postgres_mode='existing',pg_port=5433,
                python='/selected/python',vehicle_model='models/selected.pt')));config.chmod(0o600)
            with patch.dict(os.environ,{'WAYCON_CONFIG':str(config),'T1_CONSOLE_DEVICE':'cuda:2'},clear=True):
                configure(load_dsn=False)
                self.assertEqual(console_device(),'cuda:2')
                self.assertEqual(os.environ['WAYCON_POSTGRES_MODE'],'existing')
                self.assertEqual(os.environ['T1_CONSOLE_PYTHON'],'/selected/python')
                from vehicle.common.console_stack import ROOT
                self.assertEqual(model_paths()['vehicle_model'],ROOT/'models/selected.pt')
            with patch.dict(os.environ,{'T1_CONSOLE_DEVICE':'cpu'},clear=True):
                with self.assertRaises(StackUnavailable):console_device()

    def test_owned_mode_does_not_load_a_private_existing_server_dsn(self):
        with patch.dict(os.environ,{'WAYCON_POSTGRES_MODE':'owned','WAYCON_ADMIN_DSN_FILE':'/not-to-be-read'},clear=True):
            configure()
            self.assertNotIn('DB1_P4_ADMIN_DSN',os.environ)

    def test_remote_database_is_rejected_before_connecting(self):
        with patch.dict(os.environ,{'WAYCON_POSTGRES_MODE':'existing','DB1_P4_EXISTING_SERVER':'1',
                'WAYCON_PG_PORT':'5432','DB1_P4_ADMIN_DSN':'host=remote.invalid port=5432 dbname=postgres'},clear=True), \
                patch('psycopg.connect') as connect:
            with self.assertRaisesRegex(StackUnavailable,'remote databases are refused'):database_probe()
            connect.assert_not_called()

    def test_owned_postgres_uses_exact_process_handle_and_private_random_cluster(self):
        process=Mock();process.poll.return_value=None
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ,{},clear=True), \
                patch('vehicle.common.console_postgres.postgres_bin',return_value=Path('/pg16')), \
                patch('vehicle.common.console_postgres.subprocess.run',return_value=Mock(returncode=0)) as init, \
                patch('vehicle.common.console_postgres.subprocess.Popen',return_value=process) as spawn, \
                patch('psycopg.connect',return_value=nullcontext()):
            database=OwnedPostgres(directory)
            with database:
                private=database.temp
                self.assertEqual(private.stat().st_mode & 0o777,0o700)
                self.assertFalse((private/'password').exists())
                self.assertEqual(os.environ['DB1_P4_ISOLATED'],'1')
                self.assertNotIn('DB1_P4_EXISTING_SERVER',os.environ)
                self.assertIn('--auth-host=scram-sha-256',init.call_args.args[0])
                command=spawn.call_args.args[0]
                self.assertIn('127.0.0.1',command)
                self.assertNotIn('password', ' '.join(command))
            process.send_signal.assert_called_once_with(signal.SIGINT)
            self.assertFalse(private.exists())
            self.assertTrue(database.cleaned)
            self.assertNotIn('DB1_P4_ADMIN_DSN',os.environ)

    def test_initdb_failure_cleans_only_its_own_directory_and_no_server_is_started(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch('vehicle.common.console_postgres.postgres_bin',return_value=Path('/pg16')), \
                patch('vehicle.common.console_postgres.subprocess.run',return_value=Mock(returncode=1)), \
                patch('vehicle.common.console_postgres.subprocess.Popen') as spawn:
            database=OwnedPostgres(directory)
            with self.assertRaisesRegex(RuntimeError,'initdb failed'):database.__enter__()
            self.assertTrue(database.cleaned)
            self.assertIsNone(database.temp)
            self.assertTrue(Path(directory).exists())
            spawn.assert_not_called()

    def test_uncertain_web_admission_retains_owned_database_and_directory(self):
        process=Mock();process.pid=123;process.poll.return_value=None
        with tempfile.TemporaryDirectory() as directory:
            database=OwnedPostgres(directory)
            database.process=process
            database.temp=Path(directory)/'private-cluster';database.temp.mkdir()
            database.retain_for_inspection();database.close()
            process.send_signal.assert_not_called()
            self.assertTrue(database.temp.exists())
            self.assertFalse(database.cleaned)
            record=json.loads((Path(directory)/'postgres-retained.json').read_text())
            self.assertEqual(record['pid'],123)

    def test_inventory_lists_missing_prerequisites_without_installing(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ,{'WAYCON_POSTGRES_MODE':'owned','T1_MANUFACTURER_LOCK':directory+'/missing.json'},clear=True), \
                patch('vehicle.common.console_prerequisites.executable',return_value=None), \
                patch('vehicle.common.console_prerequisites.postgres_bin',return_value=None), \
                patch('vehicle.common.console_prerequisites.model_paths',return_value={'ocr_dir':Path(directory)/'ocr'}), \
                patch('vehicle.common.console_prerequisites.subprocess.run') as spawn:
            report=inventory('fixture')
            self.assertTrue(any('Node executable' in x for x in report['missing']))
            self.assertTrue(any('redis-server' in x for x in report['missing']))
            self.assertTrue(any('PostgreSQL 16' in x for x in report['missing']))
            self.assertEqual(sum('Missing ocr_dir' in x for x in report['missing']),3)
            self.assertFalse(report['manufacturer']['available'])
            spawn.assert_not_called()
