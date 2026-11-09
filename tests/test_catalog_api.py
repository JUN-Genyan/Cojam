"""Model catalog owner API, asynchronous refresh, structured settings and auth."""
import json
import sys
import time
import unittest
from unittest.mock import patch
from urllib.parse import quote

import test_contacts_access as support
from bridge import model_catalog as catalogs, registry, room as R


class CatalogAPI(unittest.TestCase):
    def setUp(self):
        registry.save_agent({'id':'catalog-test', 'driver':'cli', 'bin':[sys.executable],
                             'args':{'model':['--model','{model}'], 'thinking_budget':['--thinking','{thinking_budget}']}})
        self.room = R.rooms.create('目录 API', '', support.DATA, participants=['catalog-test'])
        self.path = '/api/r/' + quote(self.room.id) + '/'
        self.catalog = dict(catalogs.baseline(registry.get('catalog-test')), status='ready',
                            entries=[catalogs.model('configured-model', controls=catalogs.reasoning({'kind':'budget','max':8192}))])
        self.discover = patch.object(catalogs, '_discover', return_value=self.catalog)
        self.discover.start();self.addCleanup(self.discover.stop)

    def ready(self):
        status, result = support.call(self.path+'models/catalog?ai=catalog-test', owner=True)
        self.assertEqual(status,200,result)
        until = time.monotonic()+5
        while result['catalog'].get('refreshing') and time.monotonic()<until:
            time.sleep(.02)
            status,result=support.call(self.path+'models/catalog?ai=catalog-test',owner=True)
        self.assertFalse(result['catalog'].get('refreshing'))
        return result

    def test_catalog_read_requires_owner_and_does_not_expose_auth(self):
        self.assertEqual(support.call(self.path+'models/catalog?ai=catalog-test')[0],403)
        result = self.ready()
        self.assertEqual(result['models'],['configured-model'])
        self.assertNotIn('token',result['catalog'])
        self.assertEqual(support.call(self.path+'models/catalog?ai=unknown',owner=True)[0],400)

    def test_structured_budget_save_and_reset(self):
        self.ready()
        body={'ai':'catalog-test','model':'configured-model','reasoning':{'kind':'budget','value':2048}}
        self.assertEqual(support.call(self.path+'models',body,owner=True)[0],200)
        self.assertEqual(self.room.model_info('catalog-test')['reasoning'],body['reasoning'])
        body['reasoning']['value']=9000
        self.assertEqual(support.call(self.path+'models',body,owner=True)[0],409)
        self.assertEqual(support.call(self.path+'models',{'ai':'catalog-test','model':'','effort':'','reasoning':{}},owner=True)[0],200)
        self.assertEqual(self.room.meta['models']['catalog-test']['reasoning'],{})

    def test_refresh_and_uninvited_configuration_preview(self):
        self.ready()
        self.assertEqual(support.call(self.path+'models/refresh',{'ai':'catalog-test'},owner=True)[0],202)
        self.assertEqual(support.call('/api/agents/models?ai=catalog-test&room='+quote(self.room.id),owner=True)[0],200)
        self.assertEqual(support.call('/api/agents/installation',{'ai':'catalog-test','installation':'missing'},owner=True)[0],400)

    def test_room_poll_does_not_start_discovery(self):
        with patch.object(catalogs, 'get', side_effect=AssertionError('state polling must not probe')):
            self.room.api_state()


if __name__ == '__main__':
    unittest.main()
