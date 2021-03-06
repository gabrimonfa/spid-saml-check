# Copyright 2019 AgID - Agenzia per l'Italia Digitale
#
# Licensed under the EUPL, Version 1.2 or - as soon they will be approved by
# the European Commission - subsequent versions of the EUPL (the "Licence").
#
# You may not use this work except in compliance with the Licence.
#
# You may obtain a copy of the Licence at:
#
#    https://joinup.ec.europa.eu/software/page/eupl
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the Licence is distributed on an "AS IS" basis, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# Licence for the specific language governing permissions and limitations
# under the Licence.

import json
import os
import subprocess
import unittest
import datetime

from io import BytesIO
from lxml import etree as ET

from common import constants
from common import dump_pem
import common.helpers
import common.wrap
import urllib.parse
import requests
import time

METADATA = os.getenv('SP_METADATA', None)
DATA_DIR = os.getenv('DATA_DIR', './data')
SSLLABS_FORCE_NEW = int(os.getenv('SSLLABS_FORCE_NEW', 0))
SSLLABS_SKIP = int(os.getenv('SSLLABS_SKIP', 0))
SSLLABS_RETRY_INTERVAL = 10
SSLLABS_PARALLELISM_LIMIT = 10

API = 'https://api.ssllabs.com/api/v3/'



def ssllabs_api(path, payload={}):
    url = API + path

    try:
        response = requests.get(url, params=payload)
    except requests.exception.RequestException:
        sys.stderr.write('Request failed.')
        sys.exit(1)
    data = response.json()
    return data


def ssllabs_setup_new_scan(host, publish='off', startNew='on', all='done', ignoreMismatch='on'):
    path = 'analyze'
    payload = {
                'host': host,
                'publish': publish,
                'startNew': startNew,
                'all': all,
                'ignoreMismatch': ignoreMismatch
              }
    results = ssllabs_api(path, payload)
    return 1

def ssllabs_analysis(host, publish='off', startNew='off', fromCache='on', all='done', ignoreMismatch='on'):
    path = 'analyze'
    payload = {
        'host': host,
        'publish': publish,
        'startNew': startNew,
        'fromCache': fromCache,
        'all': all,
        'ignoreMismatch': ignoreMismatch
    }

    results = ssllabs_api(path, payload)

    if 'status' in results:
        while results['status'] != 'READY' and results['status'] != 'ERROR':
            time.sleep(SSLLABS_RETRY_INTERVAL)
            results = ssllabs_api(path, payload)
    return results


def ssllabs_from_cache(host, publish='off', all='done', ignoreMismatch='on'):
    results = ssllabs_analysis(host, publish, 'off', 'on', all, ignoreMismatch)
    return results

def ssllabs_new_scan(host, publish='off', all='done', ignoreMismatch='on'):
    results = ssllabs_analysis(host, publish, 'on', 'off', all, ignoreMismatch)
    return results

class TestSPMetadata(unittest.TestCase, common.wrap.TestCaseWrap):
    longMessage = False

    @classmethod
    def tearDownClass(cls):
        fname = '%s/sp-metadata-strict.json' % DATA_DIR
        with open(fname, 'w') as f:
            f.write(json.dumps(cls.report, indent=2))
            f.close()

    def setUp(self):
        self.failures = []
        _report = self.__class__.report
        paths = self.id().split('.')
        c = 1
        for path in paths:
            if path not in _report:
                if c == len(paths):
                    _report[path] = {
                        'description': self.shortDescription(),
                        'assertions': [],
                    }
                else:
                    _report[path] = {}
            _report = _report[path]
            c += 1

        if not METADATA:
            self.fail('SP_METADATA not set')

        with open(METADATA, 'rb') as md_file:
            md = md_file.read()
            md_file.close()

        self.doc = ET.parse(BytesIO(md))
        common.helpers.del_ns(self.doc)

    def tearDown(self):
        if self.failures:
            self.fail(common.helpers.dump_failures(self.failures))

    def test_xmldsig(self):
        '''Verify the SP metadata signature'''

        cmd = ' '.join(['xmlsec1',
                        '--verify',
                        '--insecure',
                        '--id-attr:ID',
                        'urn:oasis:names:tc:SAML:2.0:metadata:EntityDescriptor',
                        METADATA])
        is_valid = True
        msg = 'the metadata signature must be valid - TR pag. 19'
        try:
            subprocess.run(cmd, shell=True, check=True, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as err:
            is_valid = False
            lines = [msg]
            if err.stderr:
                stderr = (
                    'stderr: ' +
                    '\nstderr: '.join(
                        list(
                            filter(
                                None,
                                err.stderr.decode('utf-8').split('\n')
                            )
                        )
                    )
                )
                lines.append(stderr)
            if err.stdout:
                stdout = (
                    'stdout: ' +
                    '\nstdout: '.join(
                        list(
                            filter(
                                None,
                                err.stdout.decode('utf-8').split('\n')
                            )
                        )
                    )
                )
                lines.append(stdout)
            msg = '\n'.join(lines)

        self._assertTrue(is_valid, msg)

    def test_EntityDescriptor(self):
        '''Test the compliance of EntityDescriptor element'''

        e = self.doc.xpath('//EntityDescriptor')
        self._assertEqual(len(e), 1,
                          'Only one EntityDescriptor element must be present - TR pag. 19')
        self._assertTrue(('entityID' in e[0].attrib),
                         'The entityID attribute must be present - TR pag. 19')
        a = e[0].get('entityID')
        self._assertIsNotNone(a, 'The entityID attribute must have a value - TR pag. 19')

    def test_Signature(self):
        '''Test the compliance of Signature element'''

        sign = self.doc.xpath('//EntityDescriptor/Signature')
        self._assertTrue((len(sign) == 1),
                         'The Signature element must be present - TR pag. 19')

        method = sign[0].xpath('./SignedInfo/SignatureMethod')
        self._assertTrue((len(method) == 1),
                         'The SignatureMethod element must be present - TR pag. 19')

        self._assertTrue(('Algorithm' in method[0].attrib),
                         'The Algorithm attribute must be present '
                         'in SignatureMethod element - TR pag. 19')

        alg = method[0].get('Algorithm')
        self._assertIn(alg, constants.ALLOWED_XMLDSIG_ALGS,
                       (('The signature algorithm must be one of [%s] - TR pag. 19') %
                        (', '.join(constants.ALLOWED_XMLDSIG_ALGS))))

        method = sign[0].xpath('./SignedInfo/Reference/DigestMethod')
        self._assertTrue((len(method) == 1),
                         'The DigestMethod element must be present - TR pag. 19')

        self._assertTrue(('Algorithm' in method[0].attrib),
                         'The Algorithm attribute must be present '
                         'in DigestMethod element - TR pag. 19')

        alg = method[0].get('Algorithm')
        self._assertIn(alg, constants.ALLOWED_DGST_ALGS,
                       (('The digest algorithm must be one of [%s] - TR pag. 19') %
                        (', '.join(constants.ALLOWED_DGST_ALGS))))

        # save the grubbed certificate for future alanysis
        cert = sign[0].xpath('./KeyInfo/X509Data/X509Certificate')[0]
        dump_pem.dump_metadata_pem(cert, 'sp', 'signature', DATA_DIR)

    def test_SPSSODescriptor(self):
        '''Test the compliance of SPSSODescriptor element'''

        spsso = self.doc.xpath('//EntityDescriptor/SPSSODescriptor')
        self._assertTrue((len(spsso) == 1),
                         'Only one SPSSODescriptor element must be present')

        for attr in ['protocolSupportEnumeration', 'AuthnRequestsSigned']:
            self._assertTrue((attr in spsso[0].attrib),
                             'The %s attribute must be present - TR pag. 20' % attr)

            a = spsso[0].get(attr)
            self._assertIsNotNone(
                a,
                'The %s attribute must have a value - TR pag. 20' % attr
            )

            if attr == 'AuthnRequestsSigned':
                self._assertEqual(
                    a.lower(),
                    'true',
                    'The %s attribute must be true - TR pag. 20' % attr
                )

    def test_KeyDescriptor(self):
        '''Test the compliance of KeyDescriptor element(s)'''

        kds = self.doc.xpath('//EntityDescriptor/SPSSODescriptor'
                             '/KeyDescriptor[@use="signing"]')
        self._assertGreaterEqual(len(kds), 1,
                                 'At least one signing KeyDescriptor '
                                 'must be present - TR pag. 19')

        for kd in kds:
            certs = kd.xpath('./KeyInfo/X509Data/X509Certificate')
            self._assertGreaterEqual(len(certs), 1,
                                     'At least one signing x509 '
                                     'must be present - TR pag. 19')

            # save the grubbed certificate for future alanysis
            for cert in certs:
                dump_pem.dump_metadata_pem(cert, 'sp', 'signing', DATA_DIR)

        kds = self.doc.xpath('//EntityDescriptor/SPSSODescriptor'
                             '/KeyDescriptor[@use="encryption"]')

        for kd in kds:
            certs = kd.xpath('./KeyInfo/X509Data/X509Certificate')
            self._assertGreaterEqual(len(certs), 1,
                                     'At least one encryption x509 '
                                     'must be present - TR pag. 19')

            # save the grubbed certificate for future alanysis
            for cert in certs:
                dump_pem.dump_metadata_pem(cert, 'sp', 'encryption', DATA_DIR)

    def test_SingleLogoutService(self):
        '''Test the compliance of SingleLogoutService element(s)'''

        slos = self.doc.xpath('//EntityDescriptor/SPSSODescriptor'
                              '/SingleLogoutService')
        self._assertGreaterEqual(
            len(slos),
            1,
            'One or more SingleLogoutService elements must be present - AV n° 3'
        )

        for slo in slos:
            for attr in ['Binding', 'Location']:
                self._assertTrue((attr in slo.attrib),
                                 'The %s attribute '
                                 'in SingleLogoutService element '
                                 'must be present - AV n° 3' % attr)

                a = slo.get(attr)
                self._assertIsNotNone(
                    a,
                    'The %s attribute '
                    'in SingleLogoutService element '
                    'must have a value' % attr
                )

                if attr == 'Binding':
                    self._assertIn(
                        a,
                        constants.ALLOWED_SINGLELOGOUT_BINDINGS,
                        (('The %s attribute in SingleLogoutService element must be one of [%s] - AV n° 3') %  # noqa
                         (attr, ', '.join(constants.ALLOWED_BINDINGS)))  # noqa
                    )
                if attr == 'Location':
                    self._assertIsValidHttpsUrl(
                        a,
                        'The %s attribute '
                        'in SingleLogoutService element '
                        'must be a valid URL - AV n° 1 and n° 3' % attr
                    )


    def test_AssertionConsumerService(self):
        '''Test the compliance of AssertionConsumerService element(s)'''

        acss = self.doc.xpath('//EntityDescriptor/SPSSODescriptor'
                              '/AssertionConsumerService')
        self._assertGreaterEqual(len(acss), 1,
                                 'At least one AssertionConsumerService '
                                 'must be present - TR pag. 20')

        for acs in acss:
            for attr in ['index', 'Binding', 'Location']:
                self._assertTrue((attr in acs.attrib),
                                 'The %s attribute must be present - TR pag. 20' % attr)
                a = acs.get(attr)
                if attr == 'index':
                    self._assertGreaterEqual(
                        int(a),
                        0,
                        'The %s attribute must be >= 0 - TR pag. 20' % attr
                    )
                elif attr == 'Binding':
                    self._assertIn(a, constants.ALLOWED_BINDINGS,
                                   (('The %s attribute must be one of [%s] - TR pag. 20') %
                                    (attr,
                                     ', '.join(constants.ALLOWED_BINDINGS))))
                elif attr == 'Location':
                    self._assertIsValidHttpsUrl(a,
                                                'The %s attribute must be a '
                                                'valid HTTPS url - TR pag. 20 and AV n° 1' % attr)
                else:
                    pass

        acss = self.doc.xpath('//EntityDescriptor/SPSSODescriptor'
                              '/AssertionConsumerService'
                              '[@isDefault="true"]')
        self._assertTrue((len(acss) == 1),
                         'Only one default AssertionConsumerService '
                         'must be present - TR pag. 20')

        acss = self.doc.xpath('//EntityDescriptor/SPSSODescriptor'
                              '/AssertionConsumerService'
                              '[@index="0"]'
                              '[@isDefault="true"]')
        self._assertTrue((len(acss) == 1),
                         'Must be present the default AssertionConsumerService '
                         'with index = 0 - TR pag. 20')

    def test_AttributeConsumingService(self):
        '''Test the compliance of AttributeConsumingService element(s)'''

        acss = self.doc.xpath('//EntityDescriptor/SPSSODescriptor'
                              '/AttributeConsumingService')
        self._assertGreaterEqual(
            len(acss),
            1,
            'One or more AttributeConsumingService elements must be present - TR pag. 20'
        )

        for acs in acss:
            self._assertTrue(('index' in acs.attrib),
                             'The index attribute '
                             'in AttributeConsumigService element '
                             'must be present')

            idx = int(acs.get('index'))
            self._assertGreaterEqual(
                idx,
                0,
                'The index attribute in AttributeConsumigService '
                'element must be >= 0 - TR pag. 20'
            )

            sn = acs.xpath('./ServiceName')
            self._assertTrue((len(sn) > 0),
                             'The ServiceName element must be present')
            for sns in sn:        
                self._assertIsNotNone(sns.text,
                                    'The ServiceName element must have a value')

            ras = acs.xpath('./RequestedAttribute')
            self._assertGreaterEqual(
                len(ras),
                1,
                'One or more RequestedAttribute elements must be present - TR pag. 20'
            )

            for ra in ras:
                self._assertTrue(('Name' in ra.attrib),
                                 'The Name attribute in '
                                 'RequestedAttribute element '
                                 'must be present - TR pag. 20 and AV n° 6')

                self._assertIn(ra.get('Name'), constants.SPID_ATTRIBUTES,
                               (('The Name attribute '
                                 'in RequestedAttribute element '
                                 'must be one of [%s] - TR pag. 20 and AV n°6') %
                                (', '.join(constants.SPID_ATTRIBUTES))))

            al = acs.xpath('RequestedAttribute/@Name')
            self._assertEqual(
                len(al),
                len(set(al)),
                'AttributeConsumigService must not contain duplicated RequestedAttribute - TR pag. 20'
            )

    def test_Organization(self):
        '''Test the compliance of Organization element'''

        orgs = self.doc.xpath('//EntityDescriptor/Organization')
        self._assertTrue((len(orgs) <= 1),
                         'Only one Organization element can be present - TR pag. 20')

        if len(orgs) == 1:
            org = orgs[0]
            for ename in ['OrganizationName', 'OrganizationDisplayName',
                          'OrganizationURL']:
                elements = org.xpath('./%s' % ename)
                self._assertGreater(
                    len(elements),
                    0,
                    'One or more %s elements must be present - TR pag. 20' % ename
                )

                for element in elements:
                    self._assertTrue(
                        ('{http://www.w3.org/XML/1998/namespace}lang' in element.attrib),  # noqa
                        'The lang attribute in %s element must be present - TR pag. 20' % ename  # noqa
                    )

                    self._assertIsNotNone(
                        element.text,
                        'The %s element must have a value  - TR pag. 20' % ename
                    )

                    if ename == 'OrganizationURL':
                        OrganizationURLvalue = element.text.strip()
                        if not (OrganizationURLvalue.startswith('http://') or OrganizationURLvalue.startswith('https://')):
                            OrganizationURLvalue = 'https://'+OrganizationURLvalue
                        self._assertIsValidHttpUrl(
                            OrganizationURLvalue,
                            'The %s -element must be a valid URL - TR pag. 20' % ename
                        )



    @unittest.skipIf(SSLLABS_SKIP == 1, 'x')
    def test_TLS12Support(self):
        '''Test the support of TLS 1.2 for Locations URL'''
        locations = []
        completed = False
        currently_in_analysis = 0
        acs_to_check = []
        slo_to_check = []
        location_to_check = []
        index = 0
        retry_delay = 0

        start= datetime.datetime.now().replace(microsecond=0)

        acss = self.doc.xpath('//EntityDescriptor/SPSSODescriptor'
                              '/AssertionConsumerService')

        slos = self.doc.xpath('//EntityDescriptor/SPSSODescriptor'
                              '/SingleLogoutService')

        # Gather all the locations' domains
        for acs in acss:
            url = acs.get('Location')
            parsedurl = urllib.parse.urlparse(url)
            parsednetloc = parsedurl.netloc
            location_to_check.append((parsednetloc, url, 'AssertionConsumerService'))

        for slo in slos:
            url = slo.get('Location')
            parsedurl = urllib.parse.urlparse(url)
            parsednetloc = parsedurl.netloc
            location_to_check.append((parsednetloc, url, 'SingleLogoutService'))


        while (location_to_check):

            # Setting up parallel analysis according to SSLAB limits
            while (currently_in_analysis < SSLLABS_PARALLELISM_LIMIT) and (currently_in_analysis < len(location_to_check)):
                location = location_to_check[currently_in_analysis]
                domain = location[0]
                data = ssllabs_setup_new_scan(domain)
                currently_in_analysis += 1

            #Testing Locations
            while (index < currently_in_analysis):
                t = location_to_check[index]
                if (SSLLABS_FORCE_NEW == 1):
                    data = ssllabs_new_scan(t[0])
                else:
                    data = ssllabs_from_cache(t[0])
                    while data['status'] != 'ERROR' and data['status'] != 'READY':
                        if data['status'] == 'IN_PROGRESS' and ('endpoints' in data) and (
                                "eta" in data['endpoints'][0]) and (data['endpoints'][0]['eta'] > 0):
                            retry_delay = data['endpoints'][0]['eta']
                        else:
                            retry_delay = 10
                    time.sleep(retry_delay)
                    data = ssllabs_from_cache(t[0])
                self._assertIsTLS12(
                    {'location': t[1], 'data': data,
                    'service': 'AssertionConsumerService'},
                    ['A+', 'A', 'A-'],
                    '%s must be reachable and support TLS 1.2.  - AV n° 1' % t[1]
                )
                index += 1

            index = 0
            del location_to_check[:currently_in_analysis]
            currently_in_analysis = 0

            self._assertIsTLS12(
                {'location': t[1], 'data': data,
                 'service': 'SingleLogoutService'},
                ['A+', 'A', 'A-'],
                '%s must be reachable and support TLS 1.2.' % t[1]
            )

        end = datetime.datetime.now().replace(microsecond=0)
        #print('TLS evaluated in %s seconds', (end - start))
