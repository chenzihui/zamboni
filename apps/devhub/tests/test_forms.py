import json
import os

from django.conf import settings
from django.core.files.storage import default_storage as storage

import mock
from nose import SkipTest
from nose.tools import eq_
from PIL import Image

import amo
import amo.tests
from amo.tests.test_helpers import get_image_path
from amo.urlresolvers import reverse
import paypal
from applications.models import AppVersion
from addons.forms import NewPersonaForm
from addons.models import Addon, Category, Charity
from devhub import forms
from files.helpers import copyfileobj
from files.models import FileUpload
from market.models import AddonPremium
from users.models import UserProfile
from versions.models import ApplicationsVersions, License


class TestNewAddonForm(amo.tests.TestCase):

    def test_only_valid_uploads(self):
        f = FileUpload.objects.create(valid=False)
        form = forms.NewAddonForm({'upload': f.pk}, request=mock.Mock())
        assert ('There was an error with your upload. Please try again.' in
                form.errors.get('__all__')), form.errors

        f.validation = '{"errors": 0}'
        f.save()
        form = forms.NewAddonForm({'upload': f.pk}, request=mock.Mock())
        assert ('There was an error with your upload. Please try again.' not in
                form.errors.get('__all__')), form.errors


class TestContribForm(amo.tests.TestCase):

    def test_neg_suggested_amount(self):
        form = forms.ContribForm({'suggested_amount': -10})
        assert not form.is_valid()
        eq_(form.errors['suggested_amount'][0],
            'Please enter a suggested amount greater than 0.')

    def test_max_suggested_amount(self):
        form = forms.ContribForm({'suggested_amount':
                            settings.MAX_CONTRIBUTION + 10})
        assert not form.is_valid()
        eq_(form.errors['suggested_amount'][0],
            'Please enter a suggested amount less than $%s.' %
            settings.MAX_CONTRIBUTION)


class TestCharityForm(amo.tests.TestCase):

    def setUp(self):
        self.paypal_mock = mock.Mock()
        self.paypal_mock.return_value = (True, None)
        paypal.check_paypal_id = self.paypal_mock

    def test_always_new(self):
        # Editing a charity should always produce a new row.
        params = dict(name='name', url='http://url.com/', paypal='paypal')
        charity = forms.CharityForm(params).save()
        for k, v in params.items():
            eq_(getattr(charity, k), v)
        assert charity.id

        # Get a fresh instance since the form will mutate it.
        instance = Charity.objects.get(id=charity.id)
        params['name'] = 'new'
        new_charity = forms.CharityForm(params, instance=instance).save()
        for k, v in params.items():
            eq_(getattr(new_charity, k), v)

        assert new_charity.id != charity.id


class TestCompatForm(amo.tests.TestCase):
    fixtures = ['base/apps', 'base/addon_3615']

    def test_mozilla_app(self):
        moz = amo.MOZILLA
        appver = AppVersion.objects.create(application_id=moz.id)
        v = Addon.objects.get(id=3615).current_version
        ApplicationsVersions(application_id=moz.id, version=v,
                             min=appver, max=appver).save()
        fs = forms.CompatFormSet(None, queryset=v.apps.all())
        apps = [f.app for f in fs.forms]
        assert moz in apps


class TestPreviewForm(amo.tests.TestCase):
    fixtures = ['base/addon_3615']

    def setUp(self):
        self.dest = os.path.join(settings.TMP_PATH, 'preview')
        if not os.path.exists(self.dest):
            os.makedirs(self.dest)

    @mock.patch('amo.models.ModelBase.update')
    def test_preview_modified(self, update_mock):
        addon = Addon.objects.get(pk=3615)
        name = 'transparent.png'
        form = forms.PreviewForm({'caption': 'test', 'upload_hash': name,
                                  'position': 1})
        with storage.open(os.path.join(self.dest, name), 'w') as f:
            copyfileobj(open(get_image_path(name)), f)
        assert form.is_valid()
        form.save(addon)
        assert update_mock.called

    def test_preview_size(self):
        addon = Addon.objects.get(pk=3615)
        name = 'non-animated.gif'
        form = forms.PreviewForm({'caption': 'test', 'upload_hash': name,
                                  'position': 1})
        with storage.open(os.path.join(self.dest, name), 'w') as f:
            copyfileobj(open(get_image_path(name)), f)
        assert form.is_valid()
        form.save(addon)
        eq_(addon.previews.all()[0].sizes,
            {u'image': [250, 297], u'thumbnail': [126, 150]})


@mock.patch('market.models.AddonPremium.has_valid_permissions_token',
            lambda z: True)
@mock.patch('devhub.forms.check_paypal_id', lambda z: True)
class TestPremiumForm(amo.tests.TestCase):
    fixtures = ['base/addon_3615', 'base/users', 'market/prices']

    def complete(self, data, exclude, dest='payment'):
        return forms.PremiumForm(data, request=None, extra={
            'addon': Addon.objects.get(pk=3615),
            'amo_user': UserProfile.objects.get(pk=999),
            'exclude': exclude,
            'dest': dest})

    @mock.patch('devhub.forms.check_paypal_id', lambda z: True)
    @mock.patch('django.contrib.messages.warning')
    def test_remove_token(self, error):
        addon = Addon.objects.get(pk=3615)
        addon.update(paypal_id='')
        ap = AddonPremium.objects.create(paypal_permissions_token='1',
                                         addon=addon, price_id=1)
        data = {'support_email': 'foo@bar.com', 'paypal_id': 'foo@bar.com'}
        form = self.complete(data, ['price'])
        assert form.is_valid()
        form.save()
        # Do not remove the token, we had no paypal_id.
        assert AddonPremium.objects.get(pk=ap.pk).paypal_permissions_token

        data['paypal_id'] = 'fooa@bar.com'
        errmsgs = []
        error.side_effect = lambda req, msg: errmsgs.append(msg)
        form = self.complete(data, ['price'])
        # Remove the token and fail the form.
        assert not form.is_valid()
        assert not AddonPremium.objects.get(pk=ap.pk).paypal_permissions_token
        assert 'refund token' in errmsgs[0]
        eq_(len(errmsgs), 1)
        AddonPremium.objects.get(pk=ap.pk).update(paypal_permissions_token='a')
        form = self.complete(data, ['price'])
        # Do not remove the token if the paypal_id hasn't changed.
        assert form.is_valid()
        assert AddonPremium.objects.get(pk=ap.pk).paypal_permissions_token

    def test_no_paypal_id(self):
        addon = Addon.objects.get(pk=3615)
        addon.update(paypal_id='some@id.com')
        AddonPremium.objects.create(paypal_permissions_token='1',
                                    addon=addon)
        form = self.complete({}, ['paypal_id', 'support_email'])
        assert not form.is_valid()
        eq_(['price'], form.errors.keys())


class TestNewPersonaForm(amo.tests.TestCase):
    fixtures = ['base/apps', 'base/user_2519']

    def setUp(self):
        self.populate()
        self.request = mock.Mock()
        self.request.groups = ()
        self.request.amo_user = mock.Mock()
        self.request.amo_user.is_authenticated.return_value = True

    def populate(self):
        self.cat = Category.objects.create(application_id=amo.FIREFOX.id,
                                           type=amo.ADDON_PERSONA, name='xxxx')
        License.objects.create(id=amo.LICENSE_CC_BY.id)

    def get_dict(self, **kw):
        data = {
            'name': 'new name',
            'slug': 'special-slug',
            'category': self.cat.id,
            'accentcolor': '#003366',
            'textcolor': '#C0FFEE',
            'summary': 'new summary',
            'tags': 'tag1, tag2, tag3',
            'license': amo.LICENSE_CC_BY.id,
            'agreed': True,
            'header_hash': 'b4ll1n',
            'footer_hash': '5w4g'
        }
        data.update(**kw)
        return data

    def post(self, **kw):
        self.form = NewPersonaForm(self.get_dict(**kw), request=self.request)
        return self.form

    def test_name_unique(self):
        # A theme cannot share the same name as another theme's.
        Addon.objects.create(type=amo.ADDON_PERSONA, name='harry-potter')
        for name in ('Harry-Potter', '  harry-potter  ', 'harry-potter'):
            self.post(name=name)
            eq_(self.form.is_valid(), False)
            eq_(self.form.errors,
                {'name': ['This name is already in use. '
                          'Please choose another.']})

    def test_name_required(self):
        self.post(name='')
        eq_(self.form.is_valid(), False)
        eq_(self.form.errors, {'name': ['This field is required.']})

    def test_name_length(self):
        self.post(name='a' * 51)
        eq_(self.form.is_valid(), False)
        eq_(self.form.errors, {'name': ['Ensure this value has at most '
                                        '50 characters (it has 51).']})

    def test_slug_unique(self):
        # A theme cannot share the same slug as another theme's.
        Addon.objects.create(type=amo.ADDON_PERSONA, slug='harry-potter')
        for slug in ('Harry-Potter', '  harry-potter  ', 'harry-potter'):
            self.post(slug=slug)
            eq_(self.form.is_valid(), False)
            eq_(self.form.errors,
                {'slug': ['This slug is already in use. '
                          'Please choose another.']})

    def test_slug_required(self):
        self.post(slug='')
        eq_(self.form.is_valid(), False)
        eq_(self.form.errors, {'slug': ['This field is required.']})

    def test_slug_length(self):
        self.post(slug='a' * 31)
        eq_(self.form.is_valid(), False)
        eq_(self.form.errors, {'slug': ['Ensure this value has at most '
                                        '30 characters (it has 31).']})

    def test_summary_optional(self):
        self.post(summary='')
        eq_(self.form.is_valid(), True, self.form.errors)

    def test_summary_length(self):
        self.post(summary='a' * 251)
        eq_(self.form.is_valid(), False)
        eq_(self.form.errors, {'summary': ['Ensure this value has at most '
                                           '250 characters (it has 251).']})

    def test_categories_required(self):
        self.post(category='')
        eq_(self.form.is_valid(), False)
        eq_(self.form.errors, {'category': ['This field is required.']})

    def test_license_required(self):
        self.post(license='')
        eq_(self.form.is_valid(), False)
        eq_(self.form.errors, {'license': ['A license must be selected.']})

    def test_header_hash_required(self):
        self.post(header_hash='')
        eq_(self.form.is_valid(), False)
        eq_(self.form.errors, {'header_hash': ['This field is required.']})

    def test_footer_hash_required(self):
        self.post(footer_hash='')
        eq_(self.form.is_valid(), False)
        eq_(self.form.errors, {'footer_hash': ['This field is required.']})

    def test_accentcolor_optional(self):
        self.post(accentcolor='')
        eq_(self.form.is_valid(), True, self.form.errors)

    def test_accentcolor_invalid(self):
        self.post(accentcolor='#BALLIN')
        eq_(self.form.is_valid(), False)
        eq_(self.form.errors,
            {'accentcolor': ['This must be a valid hex color code, '
                             'such as #000000.']})

    def test_textcolor_optional(self):
        self.post(textcolor='')
        eq_(self.form.is_valid(), True, self.form.errors)

    def test_textcolor_invalid(self):
        self.post(textcolor='#BALLIN')
        eq_(self.form.is_valid(), False)
        eq_(self.form.errors,
            {'textcolor': ['This must be a valid hex color code, '
                           'such as #000000.']})

    def get_img_urls(self):
        return (
            reverse('devhub.personas.upload_persona', args=['persona_header']),
            reverse('devhub.personas.upload_persona', args=['persona_footer'])
        )

    def test_img_attrs(self):
        header_url, footer_url = self.get_img_urls()

        self.post()
        eq_(self.form.fields['header'].widget.attrs,
            {'data-allowed-types': 'image/jpeg|image/png',
             'data-upload-url': header_url})
        eq_(self.form.fields['footer'].widget.attrs,
            {'data-allowed-types': 'image/jpeg|image/png',
             'data-upload-url': footer_url})

    @mock.patch('addons.tasks.create_persona_preview_images.delay')
    @mock.patch('addons.tasks.save_persona_image.delay')
    def test_success(self, save_persona_image_mock,
                     create_persona_preview_images_mock):
        if not hasattr(Image.core, 'jpeg_encoder'):
            raise SkipTest

        self.request.amo_user = UserProfile.objects.get(pk=2519)

        data = self.get_dict()
        header_url, footer_url = self.get_img_urls()

        # Upload header image.
        img = open(get_image_path('persona-header.jpg'), 'rb')
        r_ajax = self.client.post(header_url, {'upload_image': img})
        data.update(header_hash=json.loads(r_ajax.content)['upload_hash'])

        # Upload footer image.
        img = open(get_image_path('persona-footer.jpg'), 'rb')
        r_ajax = self.client.post(footer_url, {'upload_image': img})
        data.update(footer_hash=json.loads(r_ajax.content)['upload_hash'])

        # Populate and save form.
        self.post()
        eq_(self.form.is_valid(), True, self.form.errors)
        self.form.save()

        addon = Addon.objects.filter(type=amo.ADDON_PERSONA).order_by('-id')[0]
        persona = addon.persona

        # Test for correct Addon and Persona values.
        eq_(unicode(addon.name), data['name'])
        eq_(addon.slug, data['slug'])
        self.assertSetEqual(addon.categories.values_list('id', flat=True),
                            [self.cat.id])
        self.assertSetEqual(addon.tags.values_list('tag_text', flat=True),
                            data['tags'].split(', '))
        eq_(persona.persona_id, 0)
        eq_(persona.license_id, data['license'])
        eq_(persona.accentcolor, data['accentcolor'].lstrip('#'))
        eq_(persona.textcolor, data['textcolor'].lstrip('#'))
        eq_(persona.author, self.request.amo_user.name)
        eq_(persona.display_username, self.request.amo_user.username)

        v = addon.versions.all()
        eq_(len(v), 1)
        eq_(v[0].version, '0')

        # Test for header, footer, and preview images.
        dst = os.path.join(settings.PERSONAS_PATH, str(addon.id))

        header_src = os.path.join(settings.TMP_PATH, 'persona_header',
                                  u'b4ll1n')
        footer_src = os.path.join(settings.TMP_PATH, 'persona_footer',
                                  u'5w4g')

        eq_(save_persona_image_mock.mock_calls,
            [mock.call(src=header_src,
                       full_dst=os.path.join(dst, 'header.png')),
             mock.call(src=footer_src,
                       full_dst=os.path.join(dst, 'footer.png'))])

        create_persona_preview_images_mock.assert_called_with(src=header_src,
            full_dst=[os.path.join(dst, 'preview.png'),
                      os.path.join(dst, 'icon.png')],
            set_modified_on=[addon])
