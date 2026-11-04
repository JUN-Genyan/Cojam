"""Export the selected Cojam ∀ / ∫ identity to SVG, ICO and PWA sizes.

Vector geometry is shared with Pillow exports; no fonts or network required.
"""
from pathlib import Path
from PIL import Image, ImageDraw
ROOT=Path(__file__).resolve().parent
STATIC=ROOT/'static'
PURPLE,LIME,CREAM='#6022E8','#D5FF3F','#FFFCF5'
S=1024
LEFT=[('M',18,17),('C',13,12,21,9,29,9),('C',35,9,37,12,39,18),('L',53,56),('L',64,22),('L',74,27),('L',55,76),('C',51,84,45,84,40,76),('L',18,31),('C',12,27,12,21,18,17),('Z',)]
INTEGRAL=[('M',79,8),('C',91,5,99,13,97,22),('C',95,30,85,31,80,25),('C',77,23,78,19,81,17),('C',71,19,69,29,67,43),('L',63,65),('C',60,84,45,96,30,91),('C',17,87,20,76,29,76),('C',36,76,39,83,34,87),('C',47,85,51,72,54,57),('L',60,27),('C',63,15,70,9,79,8),('Z',)]
BAR=[('M',17,39),('L',57,31),('C',65,29,68,39,60,42),('L',21,51),('C',11,54,8,42,17,39),('Z',)]

def path_text(commands):
    return ' '.join(c[0]+' '.join(str(v) for v in c[1:]) for c in commands)

def svg(icon=False,mono=False,dark=False):
    bg='<rect width="100" height="100" rx="25" fill="'+PURPLE+'"/>' if icon else ''
    transform='translate(8 8) scale(.84)' if icon else ''
    colors=[CREAM if icon or dark else PURPLE,LIME,LIME] if not mono else ['currentColor']*3
    paths=''.join('<path d="'+path_text(p)+'" fill="'+color+'"/>' for p,color in zip([LEFT,INTEGRAL,BAR],colors))
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><title>合拍 Cojam · ∀ + ∫</title>'+bg+'<g transform="'+transform+'">'+paths+'</g></svg>'

def points(commands):
    out=[];current=(0,0)
    for c in commands:
        if c[0] in ('M','L'): current=(c[1],c[2]);out.append(current)
        elif c[0]=='C':
            a=current;b=c[1:3];d=c[3:5];e=c[5:7]
            for i in range(1,81):
                t=i/80;out.append(tuple((1-t)**3*a[k]+3*(1-t)**2*t*b[k]+3*(1-t)*t*t*d[k]+t**3*e[k] for k in (0,1)))
            current=e
    return out

def render(maskable=False,plain=False):
    im=Image.new('RGBA',(S,S),PURPLE if maskable else (0,0,0,0));draw=ImageDraw.Draw(im)
    if not plain:draw.rounded_rectangle((0,0,S-1,S-1),S*.25,fill=PURPLE)
    scale=.74 if maskable else (.84 if not plain else .96)
    offset=(1-scale)*S/2
    for commands,color in zip([LEFT,INTEGRAL,BAR],[PURPLE if plain else CREAM,LIME,LIME]):
        draw.polygon([(offset+x*S/100*scale,offset+y*S/100*scale) for x,y in points(commands)],fill=color)
    return im

def main():
    STATIC.mkdir(exist_ok=True)
    for name,body in [('logo.svg',svg()),('logo-app.svg',svg(icon=True)),('logo-dark.svg',svg(dark=True)),('logo-mono.svg',svg(mono=True))]:(STATIC/name).write_text(body,encoding='utf-8')
    im=render()
    im.resize((256,256),Image.Resampling.LANCZOS).save(ROOT/'roundtable.ico',sizes=[(n,n) for n in (16,24,32,48,64,128,256)])
    im.resize((512,512),Image.Resampling.LANCZOS).save(ROOT/'roundtable_preview.png')
    for n in (192,512):im.resize((n,n),Image.Resampling.LANCZOS).save(STATIC/f'icon-{n}.png')
    render(True).resize((512,512),Image.Resampling.LANCZOS).save(STATIC/'icon-maskable-512.png')
    print('Generated Cojam vector mark, Windows ICO and PWA icons')
if __name__=='__main__':main()
