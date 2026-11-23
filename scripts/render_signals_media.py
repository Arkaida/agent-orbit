#!/usr/bin/env python3
"""Static product art from synthetic native exports. Requires Pillow; no video.

The guide is an explicitly labelled schematic. The hero embeds the actual app
renderer fed by DemoSupport.exportSignals, never a desktop or live session.
"""
import argparse
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
COLORS = dict(output='#63D9FF', io='#FF91B5', tools='#FFD187', network='#B99CFF', memory='#7CE3C0')
WHITE, MUTED = '#E5F1FF', '#91A7C3'


def font(size, cjk=False):
    path = '/System/Library/Fonts/STHeiti Light.ttc' if cjk else '/System/Library/Fonts/Supplemental/Arial.ttf'
    return ImageFont.truetype(path, size)


def guide(destination):
    output = [12, 22, 9, 45, 92, 31, 15, 62, 128, 58, 27, 18, 80, 150, 64, 24, 45]
    io = [5, 9, 24, 15, 30, 80, 46, 14, 8, 106, 70, 21, 12, 40, 82, 16, 55]
    network = [8, 16, 34, 18, 8, 14, 72, 94, 45, 14, 30, 68, 43, 12, 22, 62, 36]
    xs = [96 + i * 47 for i in range(17)]
    baseline = 434
    polygon = ' '.join(f'{x},{baseline-y}' for x, y in zip(xs, output))
    netline = ' '.join(f'{x},{baseline-y}' for x, y in zip(xs, network))
    bars = ''.join(f'<path d="M{x} {baseline}V{baseline-y}" stroke="{COLORS["io"]}" stroke-width="6" stroke-dasharray="5 3"/>' for x, y in zip(xs, io))
    tools = ''
    for index, height in [(3, 104), (7, 158), (12, 78), (15, 120)]:
        x, y = xs[index], baseline-height
        tools += f'<path d="M{x} {baseline}V{y}" stroke="{COLORS["tools"]}" stroke-width="2"/><path d="M{x} {y-5}l5 5-5 5-5-5Z" fill="{COLORS["tools"]}"/>'
    legend = ''
    for y, role, title, description in [(238,'output','输出光带','终端与主日志的变化'),(312,'io','I/O 分段柱','根进程系统调用读写'),(386,'tools','工具脉冲','已返回的工具结果'),(460,'network','网络细线','已关联 TCP 字节变化'),(534,'memory','内存标记','进程树 RSS · 静态')]:
        legend += f'<rect x="998" y="{y-12}" width="4" height="44" rx="2" fill="{COLORS[role]}"/><text x="1022" y="{y+3}" font-size="22" fill="{WHITE}">{title}</text><text x="1022" y="{y+29}" font-size="17" fill="{MUTED}">{description}</text>'
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="830" viewBox="0 0 1440 830" role="img" aria-labelledby="title desc">
<title id="title">Agent Orbit 声浪阅读指南</title><desc id="desc">四个动态通道在同一基线叠加，内存静态显示。图形为示意数据。</desc>
<defs><linearGradient id="bg" x2="1" y2="1"><stop stop-color="#07101F"/><stop offset="1" stop-color="#0D1D2D"/></linearGradient><linearGradient id="fill" x2="0" y2="1"><stop stop-color="{COLORS['output']}" stop-opacity=".48"/><stop offset="1" stop-color="{COLORS['output']}" stop-opacity=".05"/></linearGradient></defs>
<rect width="1440" height="830" rx="26" fill="url(#bg)"/>
<g font-family="-apple-system,BlinkMacSystemFont,Arial,'PingFang SC','Microsoft YaHei',sans-serif">
<text x="56" y="58" font-size="15" letter-spacing="3" fill="{COLORS['output']}">AGENT ORBIT / SIGNAL ATLAS</text>
<text x="56" y="120" font-size="42" font-weight="600" fill="{WHITE}">一圈声浪，五种线索。</text>
<text x="56" y="157" font-size="20" fill="{MUTED}">看形状辨认来源，看高低辨认该通道最近的强弱。</text>
<rect x="56" y="197" width="868" height="348" rx="18" fill="#091422" stroke="#21374B"/>
<text x="80" y="230" font-size="14" letter-spacing="1" fill="{MUTED}">同一圆周展开示意 · 非实时数据</text>
<path d="M96 434H848" stroke="#4E6982" stroke-width="1"/>
<path d="M96 434 {''.join(f'L{x} {baseline-y} ' for x,y in zip(xs,output))}L848 434Z" fill="url(#fill)"/>
<polyline points="{polygon}" fill="none" stroke="{COLORS['output']}" stroke-width="2" opacity=".75"/>
{bars}<polyline points="{netline}" fill="none" stroke="{COLORS['network']}" stroke-width="2"/>{tools}
<path d="M96 441H294" stroke="{COLORS['memory']}" stroke-width="3" stroke-linecap="round"/>
<text x="96" y="487" font-size="17" fill="{MUTED}">24 秒前</text><path d="M230 481H690l-8-4m8 4-8 4" fill="none" stroke="#4E6982"/>
<text x="800" y="487" font-size="17" fill="{WHITE}">现在</text>
<text x="56" y="582" font-size="21" fill="{WHITE}">四层动态信号叠在同一圈，不分成四个同心圈。</text>
<text x="56" y="615" font-size="18" fill="{MUTED}">每个通道独立缩放；跨颜色、跨会话的高度不能直接比较。</text>
{legend}
<path d="M56 654H1384" stroke="#263B50"/>
<text x="56" y="697" font-size="22" fill="{WHITE}">静默 ≠ 未知</text><text x="56" y="732" font-size="18" fill="{MUTED}">有采样、无新增：不画波峰。无法采集：标为未观测，不补成 0。</text>
<text x="850" y="697" font-size="22" fill="{WHITE}">状态先于声浪</text><text x="850" y="732" font-size="18" fill="{MUTED}">等待、异常、离线后收起动态信号。</text>
<text x="56" y="791" font-size="14" fill="#7188A4">示意图 / 默认霓虹配色 · 实际颜色随皮肤变化 · 波峰不是 CPU、Token 或推理效率</text>
</g></svg>'''
    destination.write_text(svg)


def hero(native, destination):
    width, height = 1600, 960
    image = Image.new('RGB', (width, height)); pixels = image.load()
    for y in range(height):
        for x in range(width):
            halo = math.exp(-(((x-1180)/540)**2+((y-420)/550)**2))
            pixels[x,y] = (int(5+4*halo),int(11+17*halo),int(22+26*halo))
    d=ImageDraw.Draw(image,'RGBA')
    for x in range(40,width,48):
        for y in range(40,height,48):d.ellipse((x,y,x+1,y+1),fill=(115,168,209,27))
    def label(x,y,value,size=22,color=WHITE,cjk=False):
        d.text((x,y),value,font=font(size,cjk),fill=color)
    d.line((72,73,109,73),fill=COLORS['output'],width=3)
    label(124,59,'CLI SIGNAL MONITOR',18,COLORS['output'])
    label(70,123,'Agent Orbit',86)
    label(74,239,'看见运行，也看懂运行。',37,WHITE,True)
    label(76,307,'Codex CLI  /  Claude Code CLI',23,MUTED)
    label(76,349,'独立 macOS 应用 · 本机 + SSH 远端',23,MUTED,True)
    rows=[('output','输出光带','看见内容推进'),('io','I/O 分段柱','看见进程读写'),('tools','工具脉冲','看见工具返回'),('network','网络细线','看见 TCP 活动'),('memory','内存标记','看见静态占用')]
    for index,(key,title,desc) in enumerate(rows):
        y=431+index*58; color=COLORS[key]
        d.rounded_rectangle((77,y+4,82,y+32),radius=2,fill=color)
        label(98,y,title,22,WHITE,True);label(286,y+1,desc,20,MUTED,True)
    # Embed the actual AppKit renderer with synthetic counters, without reading a screen.
    asset=Image.open(native/'signals-neon.png').convert('RGBA')
    asset=asset.resize((650,650),Image.Resampling.LANCZOS)
    image.paste(asset,(860,106),asset);d=ImageDraw.Draw(image,'RGBA')
    label(1046,760,'NATIVE UI / DEMO DATA',14,MUTED)
    d.line((76,811,1524,811),fill=(105,156,194,60),width=1)
    for x,number,title,desc in [(76,'01','完成就看见','等待输入与异常提醒'),(581,'02','编组不分心','互斥会话，共用一个位置'),(1086,'03','活动有来源','四层动态 + 一条静态标记')]:
        label(x,843,number,16,COLORS['output']);label(x+40,837,title,24,WHITE,True)
        label(x+40,879,desc,18,MUTED,True)
    image.save(destination,optimize=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-dir',type=Path)
    parser.add_argument('--output',type=Path,default=ROOT/'docs/media')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    guide(args.output/'orbit-signals-guide.svg')
    if args.native_dir:hero(args.native_dir,args.output/'orbit-signals-hero.png')
    print('Rendered static signal guide'+(' and native product hero' if args.native_dir else ''))
