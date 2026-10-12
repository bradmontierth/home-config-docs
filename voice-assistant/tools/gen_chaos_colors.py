"""Regenerate orchestrator/chaos_colors.json (spoken name -> [hue 0-360, sat 0-100]).

Run once with webcolors in a throwaway venv:
    python3 -m venv /tmp/cv && /tmp/cv/bin/pip install webcolors
    /tmp/cv/bin/python voice-assistant/tools/gen_chaos_colors.py

Chaos always drives lights at 100% brightness, so only hue + saturation
survive. Dark shades (navy -> blue, maroon -> red), grays and browns
(-> orange) would render as something else and are dropped.
"""
import colorsys
import json
from pathlib import Path

import webcolors

OUT = Path(__file__).resolve().parents[1] / 'orchestrator' / 'chaos_colors.json'

# The original hand-tuned palette wins over CSS (CSS purple/pink/orange differ).
BASE = {'red': [0, 100], 'orange': [30, 100], 'yellow': [60, 100], 'green': [120, 100],
        'cyan': [180, 100], 'blue': [240, 100], 'purple': [270, 100],
        'magenta': [300, 100], 'pink': [330, 100], 'white': [0, 0]}

# Fun names CSS lacks, plus CSS names whose CSS value is too dark/pale to use.
EXTRAS = {'fuschia': [300, 100], 'pumpkin': [24, 100], 'tangerine': [25, 100],
          'amber': [45, 100], 'mint': [150, 45], 'seafoam': [160, 50],
          'ice blue': [195, 30], 'baby blue': [200, 35], 'lavender': [270, 35],
          'lilac': [285, 35], 'periwinkle': [230, 40], 'peach': [25, 45],
          'rose': [345, 80], 'ruby': [350, 100], 'emerald': [140, 100],
          'jade': [155, 80], 'sapphire': [225, 100], 'amethyst': [275, 70],
          'teal': [178, 100], 'indigo': [260, 100], 'red orange': [15, 100],
          'puce': [345, 33], 'mauve': [276, 31], 'blush': [342, 58], 'cerise': [343, 78],
          'scarlet': [8, 100], 'vermilion': [5, 77], 'marigold': [39, 86],
          'saffron': [45, 80], 'cerulean': [196, 100], 'cobalt': [215, 100],
          'ultramarine': [244, 93], 'electric blue': [183, 51], 'neon green': [111, 92]}

DROP = {'brown', 'sienna', 'saddlebrown', 'chocolate', 'peru', 'rosybrown', 'tan',
        'burlywood', 'sandybrown', 'darkkhaki', 'rebeccapurple'}

# Words used to split CSS run-together names ("deepskyblue" -> "deep sky blue").
WORDS = sorted('''light dark medium deep hot pale misty rosy royal steel cornflower dodger
    forest golden rod indian lawn lime spring sea sky slate olive drab violet orchid
    alice antique aqua marine azure blanched almond blue violet cadet chart reuse coral
    corn silk crimson cyan floral white ghost green yellow honey dew ivory khaki
    lavender blush lemon chiffon cream mint misty rose moccasin navajo old lace
    orange red pale papaya whip peach puff pink plum powder purple salmon sea shell
    smoke snow tomato turquoise wheat gold magenta aquamarine goldenrod
    chartreuse fuchsia fire brick'''.split(), key=len, reverse=True)
SINGLE = {'aquamarine', 'chartreuse', 'goldenrod', 'fuchsia', 'turquoise', 'lavender',
          'moccasin', 'crimson', 'tomato', 'salmon', 'orchid', 'thistle', 'bisque'}


def split_name(name):
    if name in SINGLE:
        return name
    parts, rest = [], name
    while rest:
        word = next((w for w in WORDS if rest.startswith(w)), None)
        if word is None:
            return None
        parts.append(word)
        rest = rest[len(word):]
    return ' '.join(parts)


def main():
    colors = {}
    for name in webcolors.names('css3'):
        if name in DROP or 'gray' in name or 'grey' in name:
            continue
        r, g, b = webcolors.name_to_rgb(name, 'css3')
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if s < 0.15 or v < 0.6:
            continue
        spoken = split_name(name)
        if spoken is None:
            raise SystemExit(f'cannot split CSS name {name!r}; add its words to WORDS')
        colors[spoken] = [round(h * 360) % 360, round(s * 100)]
    colors.update(EXTRAS)
    colors.update(BASE)
    OUT.write_text(json.dumps(dict(sorted(colors.items())), indent=1) + '\n')
    print(f'{len(colors)} colors -> {OUT}')


if __name__ == '__main__':
    main()
