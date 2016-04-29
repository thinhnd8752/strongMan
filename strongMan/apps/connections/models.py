import sys
from collections import OrderedDict

from django.db import models
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from strongMan.apps.certificates.models.identities import AbstractIdentity
from strongMan.apps.vici.wrapper.wrapper import ViciWrapper
from strongMan.apps.encryption import fields
from ..certificates.models import UserCertificate


class Connection(models.Model):
    state = models.BooleanField(default=False)
    profile = models.CharField(max_length=50, unique=True)
    auth = models.CharField(max_length=50)
    version = models.IntegerField()

    def dict(self):
        children = OrderedDict()
        for child in self.children.all():
            children[child.name] = child.dict()

        ike_sa = OrderedDict()
        ike_sa['remote_addrs'] = [remote_address.value for remote_address in self.remote_addresses.all()]
        ike_sa['vips'] = [vip.value for vip in self.vips.all()]
        ike_sa['version'] = self.version
        ike_sa['proposals'] = [proposal.type for proposal in self.proposals.all()]
        ike_sa['children'] = children

        for local in self.local.all():
            local = local.subclass()
            ike_sa.update(local.dict())

        for remote in self.remote.all():
            remote = remote.subclass()
            ike_sa.update(remote.dict())

        connection = OrderedDict()
        connection[self.profile] = ike_sa
        return connection

    def start(self):
        vici_wrapper = ViciWrapper()
        vici_wrapper.load_connection(self.subclass().dict())

        for local in self.local.all():
            local = local.subclass()
            if local.has_private_key():
                vici_wrapper.load_key(local.get_key_dict())
            for secret in Secret.objects.filter(authentication=local):
                vici_wrapper.load_secret(secret.dict())

        for remote in self.remote.all():
            remote = remote.subclass()
            if remote.has_private_key():
                vici_wrapper.load_key(remote.get_key_dict())
            for secret in Secret.objects.filter(authentication=remote):
                vici_wrapper.load_secret(secret.dict())

        for child in self.children.all():
            reports = vici_wrapper.initiate(child.name, self.profile)
        self.state = True
        self.save()

    def stop(self):
        vici_wrapper = ViciWrapper()
        vici_wrapper.unload_connection(self.profile)
        vici_wrapper.terminate_connection(self.profile)
        self.state = False
        self.save()

    @classmethod
    def get_types(cls):
        subclasses = [subclass() for subclass in cls.__subclasses__()]
        return [subclass.get_typ() for subclass in subclasses]

    def get_typ(self):
        return type(self).__name__

    def subclass(self):
        for cls in self.get_types():
            connection_class = getattr(sys.modules[__name__], cls)
            connection = connection_class.objects.filter(id=self.id)
            if connection:
                return connection.first()
        return self

    @classmethod
    def choice_name(cls):
        raise NotImplementedError


@receiver(pre_delete, sender=Connection)
def delete_all_connected_models(sender, **kwargs):
    for child in Child.objects.filter(connection=sender):
        Proposal.objects.filter(child=child).delete()
        Address.objects.filter(local_ts=child).delete()
        Address.objects.filter(remote_ts=child).delete()
        child.delete()
    Proposal.objects.filter(connection=sender).delete()
    Address.objects.filter(local_addresses=sender).delete()
    Address.objects.filter(remote_addresses=sender).delete()
    Address.objects.filter(vips=sender).delete()

    for local in Authentication.objects.filter(local=sender):
        for secret in Secret.objects.filter(authentication=local):
            secret.delete()
        local.delete()

    for remote in Authentication.objects.filter(remote=sender):
        for secret in Secret.objects.filter(authentication=remote):
            secret.delete()
        remote.delete()


class IKEv2Certificate(Connection):
    @classmethod
    def choice_name(cls):
        return "IKEv2 Certificate"


class IKEv2EAP(Connection):
    @classmethod
    def choice_name(cls):
        return "IKEv2 EAP (Username/Password)"


class IKEv2CertificateEAP(Connection):
    @classmethod
    def choice_name(cls):
        return "IKEv2 Certificate + EAP (Username/Password)"


class IKEv2EapTls(Connection):
    @classmethod
    def choice_name(cls):
        return "IKEv2 EAP-TLS (Certificate)"


class Child(models.Model):
    name = models.CharField(max_length=50)
    mode = models.CharField(max_length=50)
    connection = models.ForeignKey(Connection, null=True, blank=True, default=None, related_name='children')

    def dict(self):
        child = OrderedDict()
        child['remote_ts'] = [remote_t.value for remote_t in self.remote_ts.all()]
        child['esp_proposals'] = [esp_proposal.type for esp_proposal in self.esp_proposals.all()]
        return child


class Address(models.Model):
    value = models.CharField(max_length=50)
    local_ts = models.ForeignKey(Child, null=True, blank=True, default=None, related_name='local_ts')
    remote_ts = models.ForeignKey(Child, null=True, blank=True, default=None, related_name='remote_ts')
    remote_addresses = models.ForeignKey(Connection, null=True, blank=True, default=None,
                                         related_name='remote_addresses')
    local_addresses = models.ForeignKey(Connection, null=True, blank=True, default=None, related_name='local_addresses')
    vips = models.ForeignKey(Connection, null=True, blank=True, default=None, related_name='vips')


class Proposal(models.Model):
    type = models.CharField(max_length=200)
    child = models.ForeignKey(Child, null=True, blank=True, default=None, related_name='esp_proposals')
    connection = models.ForeignKey(Connection, null=True, blank=True, default=None, related_name='proposals')


class Authentication(models.Model):
    local = models.ForeignKey(Connection, null=True, blank=True, default=None, related_name='local')
    remote = models.ForeignKey(Connection, null=True, blank=True, default=None, related_name='remote')
    name = models.CharField(max_length=50)  # starts with remote-* or local-*
    auth = models.CharField(max_length=50)
    auth_id = models.CharField(max_length=50, null=True, blank=True, default=None)
    round = models.IntegerField(default=1)
    ca_cert = models.ForeignKey(UserCertificate, null=True, blank=True, default=None, related_name='ca_cert_%(app_label)s_%(class)s')
    ca_identity = models.TextField()

    def dict(self):
        parameters = OrderedDict(auth=self.auth, round=self.round)
        if self.auth_id is not None:
            parameters['id'] = self.auth_id
        if self.ca_cert is not None:
            parameters['certs'] = [self.ca_cert.der_container]
        auth = OrderedDict()
        auth[self.name] = parameters

        return auth

    @classmethod
    def get_types(cls):
        subclasses = [subclass() for subclass in cls.__subclasses__()]
        return [subclass.get_typ() for subclass in subclasses]

    def get_typ(self):
        return type(self).__name__

    def subclass(self):
        for cls in self.get_types():
            authentication_class = getattr(sys.modules[__name__], cls)
            authentication = authentication_class.objects.filter(id=self.id)
            if authentication:
                return authentication.first()
        return self

    def has_private_key(self):
        return False

    def get_key_dict(self):
        pass


class EapAuthentication(Authentication):
    eap_id = models.CharField(max_length=50)

    def dict(self):
        auth = super(EapAuthentication, self).dict()
        values = auth[self.name]
        values['id'] = self.eap_id
        values['eap_id'] = self.eap_id
        return auth


class CertificateAuthentication(Authentication):
    identity = models.ForeignKey(AbstractIdentity, null=True, blank=True, default=None, related_name='identity')

    def dict(self):
        auth = super(CertificateAuthentication, self).dict()
        values = auth[self.name]
        values['certs'].append(self.identity.subclass().certificate.der_container)
        return auth

    def has_private_key(self):
        return self.identity.subclass().certificate.subclass().has_private_key

    def get_key_dict(self):
        key = self.identity.subclass().certificate.subclass().private_key
        return OrderedDict(type=str(key.algorithm).upper(), data=key.der_container)


class EapTlsAuthentication(Authentication):
    eap_id = models.CharField(max_length=50)
    identity = models.ForeignKey(AbstractIdentity, null=True, blank=True, default=None, related_name='tls_identity')

    def dict(self):
        auth = super(EapTlsAuthentication, self).dict()
        values = auth[self.name]
        values['certs'].append(self.identity.subclass().certificate.der_container)
        values['id'] = 'eap-tls-only'  # TODO: Ask Tobias for better Idea str(self.identity.subclass().value())
        values['eap_id'] = str(self.identity.subclass().value())
        #values['aaa_id'] = str(self.identity_ca.subclass().value())
        #values['aaa_id'] = 'C=CH, O=Linux strongSwan, CN=moon.strongswan.org'
        return auth

    def has_private_key(self):
        return self.identity.subclass().certificate.subclass().has_private_key

    def get_key_dict(self):
        key = self.identity.subclass().certificate.subclass().private_key
        return OrderedDict(type=str(key.algorithm).upper(), data=key.der_container)


class Secret(models.Model):
    type = models.CharField(max_length=50)
    data = fields.EncryptedCharField(max_length=50)
    authentication = models.ForeignKey(Authentication, null=True, blank=True, default=None, related_name='authentication')

    def dict(self):
        eap_id = self.authentication.subclass().eap_id
        secrets = OrderedDict(type=self.type, data=self.data, id=eap_id)
        return secrets
