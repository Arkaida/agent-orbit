#!/usr/bin/env python3
"""Compose marketing media from the app's synthetic native demo exports.
Requires Pillow for authors only; never reads the desktop or live sessions.
"""
import argparse
from pathlib import Path
import math
import shutil
import subprocess
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
W, H, FPS, SECONDS = 1280, 800, 24, 32
LATIN = '/System/Library/Fonts/Supplemental/Arial.ttf'
CJK = '/System/Library/Fonts/STHeiti Light.ttc'
CYAN, WHITE, MUTED = '#3dd9ff', '#e5f3ff', '#8aa3c0'


def font(size, chinese=False):
    return ImageFont.truetype(CJK if chinese else LATIN, size)


def text(draw, xy, value, size=24, fill=WHITE, chinese=False):
    draw.text(xy, value, font=font(size, chinese), fill=fill, spacing=18)


def backdrop():
    image = Image.new('RGB', (W, H))
    pixels = image.load()
    for y in range(H):
        for x in range(W):
            glow = math.exp(-(((x-950)/550)**2 + ((y-380)/480)**2))
            pixels[x,y] = (int(5+3*glow), int(10+17*glow), int(21+27*glow))
    draw = ImageDraw.Draw(image, 'RGBA')
    for x in range(0,W,64): draw.line((x,0,x,H),fill=(90,160,230,9))
    for y in range(0,H,64): draw.line((0,y,W,y),fill=(90,160,230,9))
    draw.ellipse((650,85,1300,735),outline=(61,217,255,25),width=1)
    draw.ellipse((690,125,1260,695),outline=(61,217,255,15),width=1)
    return image


def place(image, asset, xy, width):
    asset = asset.convert('RGBA')
    height = round(asset.height*width/asset.width)
    asset = asset.resize((width,height), Image.Resampling.LANCZOS)
    image.paste(asset,xy,asset)


def icon(output):
    size=1024
    im=Image.new('RGBA',(size,size)); d=ImageDraw.Draw(im)
    d.rounded_rectangle((34,34,990,990),radius=218,fill='#091525',outline='#244461',width=4)
    glow=Image.new('RGBA',im.size);gd=ImageDraw.Draw(glow)
    gd.ellipse((195,195,829,829),outline='#19d6ff',width=26)
    im=Image.alpha_composite(im,glow.filter(ImageFilter.GaussianBlur(35)));d=ImageDraw.Draw(im)
    d.ellipse((202,202,822,822),outline='#3dd9ff',width=12)
    d.ellipse((274,274,750,750),outline='#294765',width=5)
    d.arc((144,330,880,694),12,205,fill='#9a80ff',width=14)
    d.arc((144,330,880,694),220,353,fill='#9a80ff',width=14)
    for x,y,c,r in [(512,202,'#57ffb2',40),(790,650,'#ffba59',34),(219,650,'#c3a1ff',28)]:
        d.ellipse((x-r,y-r,x+r,y+r),fill=c)
    d.polygon([(415,617),(507,399),(536,399),(626,617),(580,617),(520,465),(461,617)],fill='#e6f4ff')
    d.rounded_rectangle((471,554,573,585),radius=12,fill='#e6f4ff')
    im.save(output/'icon.png')
    iconset=ROOT/'.media-build/AgentOrbit.iconset';iconset.mkdir(parents=True,exist_ok=True)
    for n in (16,32,128,256,512):
        for scale in (1,2):
            filename='icon_{}x{}{}.png'.format(n,n,'@2x' if scale==2 else '')
            im.resize((n*scale,n*scale),Image.Resampling.LANCZOS).save(iconset/filename)
    im.save(ROOT/'desktop/AgentOrbit.icns',format='ICNS')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--input',type=Path,required=True);args=parser.parse_args()
    output=ROOT/'docs/media';output.mkdir(parents=True,exist_ok=True)
    frames=ROOT/'.media-build/frames';frames.mkdir(parents=True,exist_ok=True)
    images={name:Image.open(args.input/(name+'.png')).convert('RGBA') for name in ['panel-neon','panel-aurora','panel-graphite','panel-daylight','panel-error','panel-running']}
    orbits=[Image.open(args.input/('orbit-%02d.png'%i)).convert('RGBA') for i in range(24)]
    mini_path=args.input/'orbit-mini.png'
    mini=Image.open(mini_path).convert('RGBA') if mini_path.exists() else None
    base=backdrop();icon(output)
    hero=base.copy();d=ImageDraw.Draw(hero)
    text(d,(74,100),'AGENT ORBIT',22,CYAN)
    text(d,(70,155),'Agent Orbit',82)
    text(d,(74,276),'让 CLI Agent 的状态，一眼可见',34,chinese=True)
    text(d,(74,340),'Codex CLI + Claude Code CLI',23,MUTED)
    for x,label in [(74,'NATIVE macOS'),(280,'LOCAL + SSH'),(472,'OPEN SOURCE')]:
        d.rounded_rectangle((x,414,x+176,452),radius=19,fill='#0d2639',outline='#23506a')
        text(d,(x+15,424),label,17,CYAN)
    text(d,(74,535),'运行  /  完成  /  异常',25,chinese=True)
    text(d,(74,588),'无需插件 · 独立运行 · 只读采集',22,MUTED,True)
    place(hero,images['panel-neon'],(850,68),404)
    place(hero,orbits[10],(650,465),266)
    text(d,(74,748),'github.com/Arkaida/agent-orbit',20,MUTED)
    text(d,(970,758),'DEMO DATA',13,MUTED)
    hero.save(output/'hero.png');hero.save(output/'demo-poster.png')
    themes=Image.new('RGB',(1280,624),'#060b16');d=ImageDraw.Draw(themes)
    for i,(key,title) in enumerate([('neon','NEON'),('aurora','AURORA'),('graphite','GRAPHITE'),('daylight','DAYLIGHT')]):
        x=24+i*316
        text(d,(x+8,26),title,19,CYAN)
        place(themes,images['panel-'+key],(x,70),286)
    themes.save(output/'themes.png')
    styles = [('bars','全息分段柱'), ('curve','连续曲线'), ('dots','节奏光点'),
              ('plasma','等离子双波'), ('aurora','极光光带'), ('radar','雷达涟漪')]
    if all((args.input / ('orbit-wave-' + key + '.png')).exists() for key, _ in styles):
        waves = Image.new('RGB', (1280, 840), '#060b16'); wave_draw = ImageDraw.Draw(waves)
        text(wave_draw, (32, 22), '真实输出活动 · 六种外围声浪', 27, CYAN, True)
        for index, (key, title) in enumerate(styles):
            x = 24 + index % 3 * 420; y = 82 + index // 3 * 370
            text(wave_draw, (x + 12, y), title, 22, WHITE, True)
            text(wave_draw, (x + 12, y + 32), key.upper(), 14, MUTED)
            place(waves, Image.open(args.input / ('orbit-wave-' + key + '.png')), (x + 72, y + 66), 270)
        text(wave_draw, (32, 810), 'DEMO DATA / Waveforms show output activity, not hardware or model performance.', 15, MUTED)
        waves.save(output / 'wave-styles.png')
    gifs=[]
    for frame in range(FPS*SECONDS):
        t=frame/FPS; image=base.copy();draw=ImageDraw.Draw(image)
        text(draw,(64,47),'AGENT ORBIT',20,CYAN)
        text(draw,(64,740),'github.com/Arkaida/agent-orbit',18,MUTED)
        text(draw,(1030,748),'DEMO / SYNTHETIC DATA',12,MUTED)
        if t<5:
            headline='让 CLI Agent\n都在视线之内';subtitle='Codex CLI · Claude Code CLI';detail='本机与远端 CLI 会话，常驻桌面监控';panel='panel-running'
        elif t<11:
            headline='别让会话\n等太久';subtitle='分会话计时 · 久等提醒';detail='查看各自速度与等待时长，及时回到终端';panel='panel-running' if t<8 else 'panel-neon'
        elif t<17:
            headline='异常，\n及时看见';subtitle='红色状态 · 可选提示音';detail='服务错误、中断或会话退出，集中查看';panel='panel-error'
        elif t<23:
            headline='小一点，\n也看得见';subtitle='单击切大小 · 双击打开面板';detail='小号球保留待响应与最久等待，拖动移动';panel=None
        elif t<29:
            headline='换一种\n桌面风格';subtitle='四套皮肤 · 可调声浪';detail='样式、配色、高度与密度，自由调整';panel=['panel-neon','panel-aurora','panel-graphite','panel-daylight'][min(3,int((t-23)/1.5))]
        else:
            headline='独立运行，\n双击即用';subtitle='无需 Python、Node.js 或 Codex 插件';detail='Agent Orbit · macOS 13+ · MIT';panel='panel-neon'
        text(draw,(64,177),headline,61,chinese=True)
        text(draw,(68,375),subtitle,26,CYAN,True)
        text(draw,(68,434),detail,21,MUTED,True)
        if panel:
            place(image,images[panel],(842,81),390)
            place(image,orbits[min(23,int(t))],(650,479),240)
        else:
            if mini is not None and t >= 20:
                place(image,mini,(902,348),144)
            else:
                place(image,orbits[min(23,int(t))],(748,195),450)
        draw=ImageDraw.Draw(image)
        draw.rounded_rectangle((64,540,500,602),radius=15,fill='#0d2739',outline='#275774')
        text(draw,(85,561),'只读监控  /  会话环  /  完成提醒',21,CYAN,True)
        draw.rectangle((0,H-4,round(W*(t/SECONDS)),H),fill=CYAN)
        image.save(frames/('%05d.png'%frame),compress_level=2)
        if frame%12==0:
            gif=image.resize((720,450),Image.Resampling.LANCZOS).quantize(colors=96,method=Image.Quantize.MEDIANCUT)
            gifs.append(gif)
    gifs[0].save(output/'demo.gif',save_all=True,append_images=gifs[1:],duration=500,loop=0,optimize=True)
    print('Prepared {} video frames and sanitized README media'.format(FPS*SECONDS))

if __name__=='__main__':main()
