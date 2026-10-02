from django.template.defaultfilters import stringfilter
from django.template.defaulttags import register

# from django import template
# register = template.Library()
# from library.models import CadevilDocument


@register.filter(name="max_value")
def max_value(key: str, data: list):
    accumulator = 0

    for i, item in enumerate(data):
        if type(item.properties[key]) not in (int, float):
            return "N/A"
        else:
            print(i)
            accumulator += item.properties[key]

    return accumulator / len(data)
