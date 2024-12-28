from django.template.defaultfilters import stringfilter
from django.template.defaulttags import register

# from django import template
# register = template.Library()
# from ...library.models import CadevilDocument


@register.filter
def format_item(to_format):
    if isinstance(to_format, list):
        if len(to_format) == 1:
            return int(to_format[0])
        if len(to_format) == 2:
            return f"{round(to_format[0], 2)} {to_format[1]}"
    if isinstance(to_format, int):
        return str(to_format)
    if isinstance(to_format, str):
        return to_format
    return str(list[0]).split(".")[0] + " " + str(list[1])