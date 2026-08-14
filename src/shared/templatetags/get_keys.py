import pprint

from django.template.defaultfilters import stringfilter
from django.template.defaulttags import register

# from django import template
# register = template.Library()
# from ...library.models import CadevilDocument


@register.filter
def get_keys(dictionary):
    pprint.pprint(dictionary.__dict__)
    return [field.name for field in dictionary._meta.get_fields()]
