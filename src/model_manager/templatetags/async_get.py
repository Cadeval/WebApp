from django import template

register = template.Library()


@register.filter
def async_get(value):
    """
    Asynchronously return the value of a django model instance.

    :param value: The text or numeric value to display.
    :return: The django model view.
    """
    return list(value.all())
