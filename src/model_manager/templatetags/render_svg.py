from django.template.defaultfilters import stringfilter
from django.template.defaulttags import register


# from django import template
# register = template.Library()
# from library.models import CadevilDocument


@register.filter(name="render_svg")
def render_svg(
        value,
        width=100,
        height=200,
        color_start="red",
        color_end="blue",
        font_size=60
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

    # Define the SVG markup
    svg_template = f"""
<svg version="1.1" baseProfile="full"
     width="auto" height="auto"
     xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="textGradient" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="{color_start}" />
      <stop offset="100%" stop-color="{color_end}" />
    </linearGradient>
  </defs>

  <text x="50%" y="50%"
        text-anchor="middle"
        alignment-baseline="middle"
        font-size="{font_size}"
        fill="url(#textGradient)">
    {text_value}
  </text>
</svg>
"""
    return svg_template.strip()
