# Copyright 2019 AgID - Agenzia per l'Italia Digitale
#
# Licensed under the EUPL, Version 1.2 or - as soon they will be approved by
# the European Commission - subsequent versions of the EUPL (the "Licence").
#
# You may not use this work except in compliance with the Licence.
#
# You may obtain a copy of the Licence at:
#
#	 https://joinup.ec.europa.eu/software/page/eupl
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the Licence is distributed on an "AS IS" basis, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# Licence for the specific language governing permissions and limitations
# under the Licence.

import base64
import json
import os
import re
import subprocess
import unittest
import urllib.parse
import zlib

from io import BytesIO
from lxml import etree as ET
from common import constants

import common.constants
import common.dump_pem as dump_pem
import common.helpers
import common.regex
import common.wrap

REQUEST = os.getenv('AUTHN_REQUEST', None)
METADATA = os.getenv('SP_METADATA', None)
DATA_DIR = os.getenv('DATA_DIR', './data')
DEBUG = int(os.getenv('DEBUG', 0))


class TestAuthnRequest(unittest.TestCase, common.wrap.TestCaseWrap):
    longMessage = False

    @classmethod
    def tearDownClass(cls):
        fname = '%s/sp-authn-request-strict.json' % DATA_DIR
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

        if not REQUEST:
            self.fail('AUTHN_REQUEST not set')

        req = None
        with open(REQUEST, 'rb') as f:
            req = f.read()
            f.close()

        self.params = urllib.parse.parse_qs(
            re.sub(r'[\s]', '', req.decode('utf-8'))
        )

        self.IS_HTTP_REDIRECT = False
        if 'Signature' in self.params and 'SigAlg' in self.params:
            self.IS_HTTP_REDIRECT = True

        if 'SAMLRequest' not in self.params:
            self.fail('SAMLRequest is missing')

        if self.IS_HTTP_REDIRECT:
            xml = zlib.decompress(
                base64.b64decode(self.params['SAMLRequest'][0]),
                -15
            )
        else:
            xml = base64.b64decode(self.params['SAMLRequest'][0])

        self.doc = ET.parse(BytesIO(xml))
        common.helpers.del_ns(self.doc)

        if not METADATA:
            self.fail('SP_METADATA not set')

        md = None
        with open(METADATA, 'rb') as md_file:
            md = md_file.read()
            md_file.close()
        self.md = ET.parse(BytesIO(md))
        common.helpers.del_ns(self.md)

    def tearDown(self):
        if self.failures:
            self.fail(common.helpers.dump_failures(self.failures))

    def test_xsd_and_xmldsig(self):
        '''Test if the XSD validates and if the signature is valid'''

        msg = ('The AuthnRequest must validate against XSD ' +
               'and must have a valid signature')

        cmd = ['bash',
               './script/check-request-xsd-and-signature.sh',
               'authn',
               'sp']

        is_valid = True
        try:
            p = subprocess.run(cmd, check=True, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
            if DEBUG:
                stdout = '\n'.join(
                    list(
                        filter(None, p.stdout.decode('utf-8').split('\n'))
                    )
                )
                print('\n' + stdout)
                stderr = '\n'.join(
                    list(
                        filter(None, p.stderr.decode('utf-8').split('\n'))
                    )
                )
                print('\n' + stderr)

        except subprocess.CalledProcessError as err:
            is_valid = False
            lines = [msg]

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

            msg = '\n'.join(lines)

        self._assertTrue(is_valid, msg)

    def test_AuthnRequest(self):
        '''Test the compliance of AuthnRequest element'''
        req = self.doc.xpath('/AuthnRequest')
        self._assertTrue(
            (len(req) == 1),
            'One AuthnRequest element must be present'
        )

        req = req[0]

        for attr in ['ID', 'Version', 'IssueInstant', 'Destination']:
            self._assertTrue(
                (attr in req.attrib),
                'The %s attribute must be present - TR pag. 8 ' % attr
            )

            value = req.get(attr)
            if (attr == 'ID'):
                self._assertIsNotNone(
                    value,
                    'The %s attribute must have a value - TR pag. 8 ' % attr
                )

            if (attr == 'Version'):
                exp = '2.0'
                self._assertEqual(
                    value,
                    exp,
                    'The %s attribute must be %s - TR pag. 8 ' % (attr, exp)
                )

            if (attr == 'IssueInstant'):
                self._assertIsNotNone(
                    value,
                    'The %s attribute must have a value - TR pag. 8 ' % attr
                )
                self._assertTrue(
                    bool(common.regex.UTC_STRING.search(value)),
                    'The %s attribute must be a valid UTC string - TR pag. 8 ' % attr
                )

            if (attr == 'Destination'):
                self._assertIsNotNone(
                    value,
                    'The %s attribute must have a value - TR pag. 8 ' % attr
                )
                self._assertIsValidHttpsUrl(
                    value,
                    'The %s attribute must be a valid HTTPS url - TR pag. 8 ' % attr
                )

        self._assertTrue(
            ('IsPassive' not in req.attrib),
            'The IsPassive attribute must not be present - TR pag. 9 '
        )

        level = req.xpath('//RequestedAuthnContext'
                          '/AuthnContextClassRef')[0].text
        if bool(common.regex.SPID_LEVEL_23.search(level)):
            self._assertTrue(
                ('ForceAuthn' in req.attrib),
                'The ForceAuthn attribute must be present if SPID level > 1 - TR pag. 8 '
            )
            value = req.get('ForceAuthn')
            self._assertTrue(
                (value.lower() in constants.BOOLEAN_TRUE),
                'The ForceAuthn attribute must be true or 1 - TR pag. 8 '
            )

        attr = 'AssertionConsumerServiceIndex'
        if attr in req.attrib:
            value = req.get(attr)
            availableassertionindexes = []

            acss = self.md.xpath('//EntityDescriptor/SPSSODescriptor'
                           '/AssertionConsumerService')
            for acs in acss:
                index = acs.get('index')
                availableassertionindexes.append(index)

            self._assertIsNotNone(
                value,
                'The %s attribute must have a value- TR pag. 8 ' % attr
            )
            self._assertGreaterEqual(
                int(value),
                0,
                'The %s attribute must be >= 0 - TR pag. 8 and pag. 20' % attr
            )
            self._assertTrue(value in availableassertionindexes,
                'The %s attribute must be equal to an AssertionConsumerService index - TR pag. 8 ' % attr
            )
        else:
            availableassertionlocations = []

            acss = self.md.xpath('//EntityDescriptor/SPSSODescriptor'
                                 '/AssertionConsumerService')
            for acs in acss:
                location = acs.get('Location')
                availableassertionlocations.append(location)

            for attr in ['AssertionConsumerServiceURL', 'ProtocolBinding']:
                self._assertTrue(
                    (attr in req.attrib),
                    'The %s attribute must be present - TR pag. 8 ' % attr
                )

                value = req.get(attr)

                self._assertIsNotNone(
                    value,
                    'The %s attribute must have a value - TR pag. 8 ' % attr
                )

                if attr == 'AssertionConsumerServiceURL':
                    self._assertIsValidHttpsUrl(
                        value,
                        'The %s attribute must be a valid HTTPS url - TR pag. 8 and pag. 16' % attr
                    )

                    self._assertTrue(value in availableassertionlocations,
                        'The %s attribute must be equal to an AssertionConsumerService Location - TR pag. 8 ' % attr
                    )

                if attr == 'ProtocolBinding':
                    exp = 'urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST'
                    self._assertEqual(
                        value,
                        exp,
                        'The %s attribute must be %s - TR pag. 8 ' % (attr, exp)
                    )

        attr = 'AttributeConsumingServiceIndex'
        if attr in req.attrib:
            availableattributeindexes = []

            acss = self.md.xpath('//EntityDescriptor/SPSSODescriptor'
                                 '/AttributeConsumingService')
            for acs in acss:
                index = acs.get('index')
                availableattributeindexes.append(index)

            value = req.get(attr)
            self._assertIsNotNone(
                value,
                'The %s attribute must have a value - TR pag. 8' % attr
            )
            self._assertGreaterEqual(
                int(value),
                0,
                'The %s attribute must be >= 0 - TR pag. 8 and pag. 20' % attr
            )
            self._assertTrue(value in availableattributeindexes,
                'The %s attribute must be equal to an AttributeConsumingService index - TR pag. 8 ' % attr
            )

    def test_Subject(self):
        '''Test the compliance of Subject element'''

        subj = self.doc.xpath('//AuthnRequest/Subject')
        if len(subj) > 1:
            self._assertEqual(
                len(subj),
                1,
                'Only one Subject element can be present - TR pag. 9'
            )

        if len(subj) == 1:
            subj = subj[0]
            name_id = subj.xpath('./NameID')
            self._assertEqual(
                len(name_id),
                1,
                'One NameID element in Subject element must be present - TR pag. 9'
            )
            name_id = name_id[0]
            for attr in ['Format', 'NameQualifier']:
                self._assertTrue(
                    (attr in name_id.attrib),
                    'The %s attribute must be present - TR pag. 9' % attr
                )

                value = name_id.get(attr)

                self._assertIsNotNone(
                    value,
                    'The %s attribute must have a value - TR pag. 9' % attr
                )

                if attr == 'Format':
                    exp = ('urn:oasis:names:tc:SAML:1.1:nameid-format'
                           ':unspecified')
                    self._assertEqual(
                        value,
                        exp,
                        'The % attribute must be %s - TR pag. 9' % (attr, exp)
                    )

    def test_Issuer(self):
        '''Test the compliance of Issuer element'''

        e = self.doc.xpath('//AuthnRequest/Issuer')
        self._assertTrue(
            (len(e) == 1),
            'One Issuer element must be present - TR pag. 9'
        )

        e = e[0]

        self._assertIsNotNone(
            e.text,
            'The Issuer element must have a value - TR pag. 9'
        )

        entitydescriptor = self.md.xpath('//EntityDescriptor')
        entityid = entitydescriptor[0].get('entityID')
        self._assertEqual(e.text, entityid, 'The Issuer\'s value must be equal to entityID - TR pag. 9')

        for attr in ['Format', 'NameQualifier']:
            self._assertTrue(
                (attr in e.attrib),
                'The %s attribute must be present - TR pag. 9' % attr
            )

            value = e.get(attr)

            self._assertIsNotNone(
                value,
                'The %s attribute must have a value - TR pag. 9' % attr
            )

            if attr == 'Format':
                exp = 'urn:oasis:names:tc:SAML:2.0:nameid-format:entity'
                self._assertEqual(
                    value,
                    exp,
                    'The %s attribute must be %s - TR pag. 9' % (attr, exp)
                )

    def test_NameIDPolicy(self):
        '''Test the compliance of NameIDPolicy element'''

        e = self.doc.xpath('//AuthnRequest/NameIDPolicy')
        self._assertTrue(
            (len(e) == 1),
            'One NameIDPolicy element must be present - TR pag. 9'
        )

        e = e[0]

        self._assertTrue(
            ('AllowCreate' not in e.attrib),
            'The AllowCreate attribute must not be present - AV n°5 '
        )

        attr = 'Format'
        self._assertTrue(
            (attr in e.attrib),
            'The %s attribute must be present - TR pag. 9' % attr
        )

        value = e.get(attr)

        self._assertIsNotNone(
            value,
            'The %s attribute must have a value - TR pag. 9' % attr
        )

        if attr == 'Format':
            exp = 'urn:oasis:names:tc:SAML:2.0:nameid-format:transient'
            self._assertEqual(
                value,
                exp,
                'The %s attribute must be %s - TR pag. 9' % (attr, exp)
            )

    def test_Conditions(self):
        '''Test the compliance of Conditions element'''
        e = self.doc.xpath('//AuthnRequest/Conditions')

        if len(e) > 1:
            self._assertEqual(
                len(1),
                1,
                'Only one Conditions element is allowed - TR pag. 9'
            )

        if len(e) == 1:
            e = e[0]
            for attr in ['NotBefore', 'NotOnOrAfter']:
                self._assertTrue(
                    (attr in e.attrib),
                    'The %s attribute must be present - TR pag. 9' % attr
                )

                value = e.get(attr)

                self._assertIsNotNone(
                    value,
                    'The %s attribute must have a value - TR pag. 9' % attr
                )

                self._assertTrue(
                    bool(common.regex.UTC_STRING.search(value)),
                    'The %s attribute must have avalid UTC string - TR pag. 9' % attr
                )

    def test_RequestedAuthnContext(self):
        '''Test the compliance of RequestedAuthnContext element'''

        e = self.doc.xpath('//AuthnRequest/RequestedAuthnContext')
        self._assertEqual(
            len(e),
            1,
            'Only one RequestedAuthnContext element must be present - TR pag. 9'
        )

        e = e[0]

        attr = 'Comparison'
        self._assertTrue(
            (attr in e.attrib),
            'The %s attribute must be present - TR pag. 10' % attr
        )

        value = e.get(attr)
        self._assertIsNotNone(
            value,
            'The %s attribute must have a value - TR pag. 10' % attr
        )

        allowed = ['exact', 'minimum', 'better', 'maximum']
        self._assertIn(
            value,
            allowed,
            (('The %s attribute must be one of [%s] - TR pag. 10') %
             (attr, ', '.join(allowed)))
        )

        acr = e.xpath('./AuthnContextClassRef')
        self._assertEqual(
            len(acr),
            1,
            'Only one AuthnContexClassRef element must be present - TR pag. 9'
        )

        acr = acr[0]

        self._assertIsNotNone(
            acr.text,
            'The AuthnContexClassRef element must have a value - TR pag. 9'
        )

        self._assertTrue(
            bool(common.regex.SPID_LEVEL_ALL.search(acr.text)),
            'The AuthnContextClassRef element must have a valid SPID level - TR pag. 9 and AV n°5'
        )

    def test_Signature(self):
        '''Test the compliance of Signature element'''

        if not self.IS_HTTP_REDIRECT:
            sign = self.doc.xpath('//AuthnRequest/Signature')
            self._assertTrue((len(sign) == 1),
                             'The Signature element must be present - TR pag. 10')

            method = sign[0].xpath('./SignedInfo/SignatureMethod')
            self._assertTrue((len(method) == 1),
                             'The SignatureMethod element must be present- TR pag. 10')

            self._assertTrue(('Algorithm' in method[0].attrib),
                             'The Algorithm attribute must be present '
                             'in SignatureMethod element - TR pag. 10')

            alg = method[0].get('Algorithm')
            self._assertIn(alg, common.constants.ALLOWED_XMLDSIG_ALGS,
                           (('The signature algorithm must be one of [%s] - TR pag. 10') %
                            (', '.join(common.constants.ALLOWED_XMLDSIG_ALGS))))  # noqa

            method = sign[0].xpath('./SignedInfo/Reference/DigestMethod')
            self._assertTrue((len(method) == 1),
                             'The DigestMethod element must be present')

            self._assertTrue(('Algorithm' in method[0].attrib),
                             'The Algorithm attribute must be present '
                             'in DigestMethod element - TR pag. 10')

            alg = method[0].get('Algorithm')
            self._assertIn(alg, common.constants.ALLOWED_DGST_ALGS,
                           (('The digest algorithm must be one of [%s] - TR pag. 10') %
                            (', '.join(common.constants.ALLOWED_DGST_ALGS))))

            # save the grubbed certificate for future alanysis
            cert = sign[0].xpath('./KeyInfo/X509Data/X509Certificate')[0]
            dump_pem.dump_request_pem(cert, 'authn', 'signature', DATA_DIR)

    def test_RelayState(self):
        '''Test the compliance of RelayState parameter'''

        if ('RelayState' in self.params):
            relaystate = self.params.get('RelayState')[0]
            self._assertTrue(
                (relaystate.find('http') == -1 ),
                'RelayState must not be immediately intelligible - TR pag. 14 or pag. 15'
            )
        else:
            self._assertTrue(False, 'RelayState is missing - TR pag. 14 or pag. 15')

    def test_Scoping(self):
        '''Test the compliance of Scoping element'''

        e = self.doc.xpath('//AuthnRequest/Scoping')
        self._assertEqual(
            len(e),
            0,
            'The Scoping element must not be present - AV n°5'
        )

    def test_RequesterID(self):
        '''Test the compliance of RequesterID element'''

        e = self.doc.xpath('//AuthnRequest/RequesterID')
        self._assertEqual(
            len(e),
            0,
            'The RequesterID  element must not be present - AV n°5'
        )
