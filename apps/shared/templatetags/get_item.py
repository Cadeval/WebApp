from django.template.defaultfilters import stringfilter
from django.template.defaulttags import register
from pprint import pprint

# from django import template
# register = template.Library()
# from ...library.models import CadevilDocument


@register.filter
def get_item(dictionary, key=None) -> dict:
    if isinstance(dictionary, dict):
        return dictionary.get(key)
    else:
        pprint(dictionary)
        return getattr(dictionary, key)