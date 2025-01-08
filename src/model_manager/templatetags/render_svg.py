from django.template.defaultfilters import stringfilter
from django.template.defaulttags import register


# from django import template
# register = template.Library()
# from library.models import CadevilDocument


@register.filter
def render_svg(
        value,
        font_size=3,
        color_start="red",
        color_end="blue",
):
    """
    Create an SVG string that renders the given value as text with a gradient.

    :param value: The text or numeric value to display.
    :param width: The width of the SVG canvas.
    :param height: The height of the SVG canvas.
    :param color_start: The starting color of the gradient.
    :param color_end: The ending color of the gradient.
    :param font_size: The font size for the text.
    :return: A string containing SVG markup.
    """
    text_value = str(value)  # Convert integer/float to string if necessary

    # width="{width}" height="{height}"

    # Define the SVG markup
    svg_template = f"""
<svg version="1.1" baseProfile="full"
     xmlns="http://www.w3.org/2000/svg">
  <defs>
  <text-color>{color_start}</text-color>
  </defs>

  <text x="50%" y="50%"
  
        text-anchor="middle"
        alignment-baseline="middle"
        font-size="{font_size}em"
        fill="#--primary-color"
        text-color="{color_end}">
    {text_value}
  </text>
</svg>
"""
    return svg_template.strip()
