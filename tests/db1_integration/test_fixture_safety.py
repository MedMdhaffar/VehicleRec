"""Provisioning guards and scoped cleanup; no real connections in these tests."""
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import psycopg
from psycopg.conninfo import make_conninfo

import test_postgres as fixture_module
from test_postgres import provisioning_params


class FixtureSafetyTests(unittest.TestCase):
    def env(self, mode='DB1_P4_ISOLATED', database='postgres', host='127.0.0.1'):
        return {mode:'1','DB1_P4_ADMIN_DSN':make_conninfo(host=host,dbname=database)}

    def test_absent_authorization_skips_before_connect(self):
        with patch('test_postgres.psycopg.connect') as connect:
            for env in ({},{'DB1_P4_ADMIN_DSN':make_conninfo(host='127.0.0.1',dbname='postgres')},
                        {'DB1_P4_EXISTING_SERVER':'1'}):
                with self.assertRaises(unittest.SkipTest):provisioning_params(env)
            connect.assert_not_called()

    def test_dedicated_mode_preserves_postgres_requirement(self):
        self.assertEqual(provisioning_params(self.env())['dbname'],'postgres')
        with self.assertRaises(RuntimeError):provisioning_params(self.env(database='intern'))

    def test_existing_mode_allows_only_authorized_provisioning_databases(self):
        for database in ('postgres','intern'):
            params=provisioning_params(self.env('DB1_P4_EXISTING_SERVER',database))
            self.assertEqual(params['dbname'],database)
            self.assertEqual(params['connect_timeout'],'5')
        with self.assertRaises(RuntimeError):
            provisioning_params(self.env('DB1_P4_EXISTING_SERVER','unapproved'))

    def test_conflicting_modes_rejected(self):
        with self.assertRaises(RuntimeError):
            provisioning_params({**self.env(),'DB1_P4_EXISTING_SERVER':'1'})

    def test_nonlocal_host_rejected_in_both_modes(self):
        for mode in ('DB1_P4_ISOLATED','DB1_P4_EXISTING_SERVER'):
            with self.assertRaises(RuntimeError):provisioning_params(self.env(mode,host='remote.invalid'))

    def test_invalid_connection_error_is_suppressed(self):
        with self.assertRaisesRegex(RuntimeError,'details suppressed') as caught:
            provisioning_params({'DB1_P4_EXISTING_SERVER':'1','DB1_P4_ADMIN_DSN':'invalid private input'})
        self.assertTrue(caught.exception.__suppress_context__)

    def fake(self, created):
        admin=MagicMock();cursor=admin.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value=(0,)
        return SimpleNamespace(created=created,admin=admin),cursor

    def test_partial_setup_cleanup_only_created_owner(self):
        fixture,cursor=self.fake([('role','fresh_owner')])
        fixture_module.PostgreSQL16Tests.cleanup_resources.__func__(fixture)
        self.assertEqual(len(cursor.execute.call_args_list),2)
        drop=cursor.execute.call_args_list[0].args[0].as_string()
        self.assertEqual(drop,'DROP ROLE "fresh_owner"')
        self.assertEqual(cursor.execute.call_args_list[1].args[1],('fresh_owner',))

    def test_cleanup_continues_after_failure_and_verifies_every_resource(self):
        created=[('role','fresh_owner'),('role','fresh_app'),('database','fresh_db')]
        fixture,cursor=self.fake(created)
        cursor.execute.side_effect=[psycopg.OperationalError(),None,None,None,None,None]
        with self.assertRaisesRegex(RuntimeError,'scoped P4 cleanup failed'):
            fixture_module.PostgreSQL16Tests.cleanup_resources.__func__(fixture)
        calls=cursor.execute.call_args_list
        self.assertEqual([c.args[0].as_string() for c in calls[:3]],
                         ['DROP DATABASE "fresh_db" WITH (FORCE)','DROP ROLE "fresh_app"','DROP ROLE "fresh_owner"'])
        self.assertEqual([c.args[1] for c in calls[3:]],[(name,) for _,name in created])

    def test_cleanup_rejects_remaining_catalog_resources(self):
        fixture,cursor=self.fake([('role','fresh_owner')]);cursor.fetchone.return_value=(1,)
        with self.assertRaisesRegex(RuntimeError,'scoped P4 cleanup failed'):
            fixture_module.PostgreSQL16Tests.cleanup_resources.__func__(fixture)


if __name__=='__main__':unittest.main()
