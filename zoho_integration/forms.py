from django import forms
from zoho_integration.models import Contact, Token
import uuid
from django.template.loader import render_to_string
from django.core.mail import send_mail
from django.http import HttpResponseRedirect
import accounts.backends
from django.conf import settings
import ldap
from pprint import pprint
from accounts.models import PS1User

class activate_account_form(forms.Form):
    ps1_email = forms.EmailField(label="PS1 Email")

    def clean_ps1_email(self):
        try:
            contact = Contact.objects.get(email=self.cleaned_data['ps1_email'])
        except Contact.DoesNotExist:
            raise forms.ValidationError("Unknown Email Address")
        if contact.user is not None:
            #HEFTODO an account recovery link would be nice.
            raise forms.ValidationError("Your Account has already been activated")

        return self.cleaned_data['ps1_email']

    def save(self):
        email_address = self.cleaned_data['ps1_email']
        # HEFTODO check email against AD
        zoho_contact = Contact.objects.get(email=email_address)
        token = Token(token=uuid.uuid4(), zoho_contact=zoho_contact)
        token.save()
        c = {
                'email': email_address,
                'token': token.token,
                'protocol': 'http', # HEFTODO detemine if dev or not
                'domain': 'localhost:8000' # HEFTODO determine if dev or not
        }
        subject = render_to_string("activation_email_subject.txt", c)
        subject = ''.join(subject.splitlines())
        body = render_to_string("activation_email_body.html", c)
        send_mail(subject, body, "hef@pbrfrat.com", [email_address])

class account_register_form(forms.Form):
    preferred_username = forms.CharField()
    first_name = forms.CharField()
    last_name = forms.CharField()
    preferred_email = forms.EmailField()
    password1 = forms.CharField(widget = forms.PasswordInput)
    password2 = forms.CharField(widget = forms.PasswordInput)
    token = forms.CharField(widget = forms.HiddenInput())

    def clean_preferred_username(self):
        username = self.cleaned_data['preferred_username']
        l = accounts.backends.get_ldap_connection()
        filter_string = '(sAMAccountName={0})'.format(username)
        result = l.search_s(settings.AD_BASEDN, ldap.SCOPE_SUBTREE, filterstr=filter_string)
        if result:
            error_string = "A member is already using '{0}' as his or her username.".format(username)
            raise forms.ValidationError(error_string)
        return username

    def save(self):
        """ Create the user
        A lot of this functionality needs to be moved to PS1UserManager, and
        some of the duplicate functionality needs with the accounts module
        needs to be refactored.
        """
        token = Token.objects.get(token=self.cleaned_data['token'])
        user_dn = "CN={0},{1}".format(self.cleaned_data['preferred_username'], settings.AD_BASEDN)
        user_attrs = {}
        user_attrs['objectClass'] = ['top', 'person', 'organizationalPerson', 'user']
        user_attrs['cn'] = str(self.cleaned_data['preferred_username'])
        user_attrs['userPrincipalName'] = str(self.cleaned_data['preferred_username'] + '@' + settings.AD_DOMAIN)
        user_attrs['sAMAccountName'] = str(self.cleaned_data['preferred_username'])
        user_attrs['givenName'] = str(self.cleaned_data['first_name'])
        user_attrs['sn'] = str(self.cleaned_data['last_name'])
        user_attrs['userAccountControl'] = '514'
        user_ldif = ldap.modlist.addModlist(user_attrs)

        # Prep the password
        unicode_pass = '\"' + self.cleaned_data['password1'] + '\"'
        password_value = unicode_pass.encode('utf-16-le')
        add_pass = [(ldap.MOD_REPLACE, 'unicodePwd', [password_value])]

        # prep account enable
        enable_account = [(ldap.MOD_REPLACE, 'userAccountControl', '512')]

        ldap_connection = accounts.backends.get_ldap_connection()

        # add the user to AD
        result = ldap_connection.add_s(user_dn, user_ldif)

        #now get the user guid
        filter_string = r'sAMAccountName={0}'.format(str(self.cleaned_data['preferred_username']))
        result = ldap_connection.search_ext_s(settings.AD_BASEDN, ldap.SCOPE_ONELEVEL, filterstr=filter_string)
        pprint(result)
        ldap_user = result[0][1]
        guid = ''.join('\\%02x' % ord(x) for x in ldap_user['objectGUID'][0])
        user = PS1User(object_guid=guid)
        user.save()
        token.zoho_contact.user = user
        token.zoho_contact.save()
        token.delete()


        ldap_connection.modify_s(user_dn, add_pass)
        ldap_connection.modify_s(user_dn, enable_account)

        ldap_connection.unbind_s()

        return True