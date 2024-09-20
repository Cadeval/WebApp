from django.template.defaultfilters import stringfilter
from django.template.defaulttags import register

# from django import template
# register = template.Library()
# from ...library.models import CadevilDocument


@register.filter(name="get_item")
def get_item(dictionary, key):
    return dictionary.get(key)
