"""Indexes reflect endpoint capabilities and never equate saved preferences with applied settings."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

import test_contacts_access as support
from bridge import agent_settings, agents, model_catalog, registry, room as R


class ModelCatalog(unittest.TestCase):
    def setUp(self):
        model_catalog.CACHE.clear()
        path=Path(support.DATA,'agent_settings.json')
        if path.exists():path.unlink()
        registry.load()
        agent_settings.save('codex',{'bin':[sys.executable]},agent_settings.view('codex')['revision'])
        self.r=R.rooms.create('indexed models','',support.DATA,participants=['codex'])
        self.addCleanup(R.rooms.unload,self.r.id)

    def test_codex_refresh_pages_preserves_empty_efforts_and_source(self):
        server=Mock()
        server.request.side_effect=[{'data':[{'model':'indexed-fast','supportedReasoningEfforts':[]}],'nextCursor':'next'},
                                    {'data':[{'model':'indexed-reasoning','supportedReasoningEfforts':[{'reasoningEffort':'high'}]}]}]
        with patch.object(agents,'codex_launch',return_value=(['fake'],{})),patch.object(agents,'CodexServer',return_value=server):
            result=model_catalog.refresh(self.r,'codex')
        self.assertIn('indexed-fast',result['models'])
        self.assertEqual(result['efforts_by_model']['indexed-fast'],[])
        self.assertEqual(result['efforts_by_model']['indexed-reasoning'],['high'])
        self.assertEqual(result['source'],'Codex model/list')
        self.assertFalse(self.r.set_models('codex','indexed-fast','high')[0])
        self.assertTrue(self.r.set_models('codex','indexed-reasoning','high')[0])
        server.stop.assert_called_once()

    def test_refresh_failure_retains_index_with_stale_marker(self):
        key=model_catalog._key('codex')
        model_catalog.CACHE[key]={'models':['kept-model'],'efforts_by_model':{'kept-model':['low']},'source':'previous','checked_at':1}
        with patch.object(agents,'codex_launch',side_effect=RuntimeError('offline')):
            result=model_catalog.refresh(self.r,'codex')
        self.assertIn('kept-model',result['models'])
        self.assertTrue(result['stale']);self.assertIn('offline',result['error'])

    def test_last_index_survives_backend_cache_restart(self):
        server=Mock()
        server.request.return_value={'data':[{'model':'persisted-model','supportedReasoningEfforts':[]}]}
        with patch.object(agents,'codex_launch',return_value=(['fake'],{})),patch.object(agents,'CodexServer',return_value=server):
            model_catalog.refresh(self.r,'codex')
        model_catalog.CACHE.clear()
        self.assertIn('persisted-model',model_catalog.capabilities(self.r,'codex')['models'])

    def test_busy_refresh_does_not_start_a_process(self):
        self.r.state['running'].append('codex')
        try:
            with patch.object(agents,'CodexServer') as server:
                with self.assertRaisesRegex(ValueError,'正在运行'):
                    model_catalog.refresh(self.r,'codex')
            server.assert_not_called()
        finally:self.r.state['running'].clear()

    def test_saved_requested_settings_wait_for_actual_acceptance(self):
        self.assertTrue(self.r.set_models('codex','test-model','high')[0])
        state=self.r.model_info('codex')
        self.assertEqual(state['apply_status'],'pending');self.assertIsNone(state['accepted'])
        revision=model_catalog.request_id(self.r,'codex')
        model_catalog.accepted(self.r,'codex',revision,'fake protocol acknowledgement')
        state=self.r.model_info('codex')
        self.assertEqual(state['apply_status'],'applied')
        self.assertEqual(state['accepted']['model'],'test-model')
        self.assertIsNone(state['observed'])
        self.r.set_models('codex','different-model','low')
        model_catalog.accepted(self.r,'codex',revision,'stale acknowledgement')
        self.assertEqual(self.r.model_info('codex')['apply_status'],'pending')

    def test_config_rejection_is_reported_as_failure(self):
        self.r.set_models('codex','test-model','high')
        revision=model_catalog.request_id(self.r,'codex')
        model_catalog.result(self.r,'codex',revision,False,'unsupported model: test-model','')
        state=self.r.model_info('codex')
        self.assertEqual(state['apply_status'],'failed')
        self.assertIn('unsupported',state['apply_error'])

    def test_acp_index_comes_from_real_declared_config_options(self):
        from bridge import acp
        registry.save_agent({'id':'index-acp','driver':'acp','bin':[sys.executable],'args':{'acp':['fake']}})
        self.r.set_members(['codex','index-acp'],{'codex':{},'index-acp':{}})
        process=Mock()
        process.config_options=[{'id':'model-choice','category':'model','options':[{'name':'group','options':[{'value':'vendor/model'}]}]},
                                {'id':'thinking','category':'thought_level','options':[{'value':'low'},{'value':'high'}]}]
        with patch.object(acp,'AcpProcess',return_value=process):
            result=model_catalog.refresh(self.r,'index-acp')
        self.assertTrue(result['model_supported']);self.assertTrue(result['effort_supported'])
        self.assertIn('vendor/model',result['models'])
        self.assertEqual(result['efforts_by_model']['vendor/model'],['low','high'])
        self.assertTrue(self.r.set_models('index-acp','vendor/model','high')[0])
        process.close.assert_called_once()

    def test_index_routes_require_owner_authentication(self):
        path='/api/r/'+support.quote(self.r.id)+'/models/catalog?ai=codex'
        self.assertEqual(support.call(path)[0],403)
        self.assertEqual(support.call(path,owner=True)[0],200)


if __name__=='__main__':unittest.main()
