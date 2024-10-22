from django.template.defaultfilters import stringfilter
from django.template.defaulttags import register

# from django import template
# register = template.Library()
# from ...library.models import CadevilDocument


@register.filter(name="format_item")
def format_ite(list):
    return str(list[0]).split(".")[0] + " " + str(list[1])
