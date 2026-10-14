"""PGPC layout with sanitized school artwork and per-record placeholders."""
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 640, 1000
BLUE = '#172579'
GOLD = '#e5df40'
ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / 'assets' / 'id-template'
FONT = ROOT / 'assets' / 'fonts' / 'DejaVuSansCondensed-Bold.ttf'
COURSES = {
    'BSCS': ('BACHELOR OF SCIENCE', 'IN COMPUTER SCIENCE'),
    'BSIT': ('BACHELOR OF SCIENCE', 'IN INFORMATION TECHNOLOGY'),
    'BSBA': ('BACHELOR OF SCIENCE', 'IN BUSINESS ADMINISTRATION'),
    'BSED': ('BACHELOR OF SECONDARY', 'EDUCATION'),
}


def sample_card(student, side, qr_url=None):
    if side not in ('front','back'):
        raise ValueError('Card side must be front or back.')
    image = Image.new('RGB', (WIDTH, HEIGHT), BLUE if side == 'front' else '#f4f4f5')
    draw = ImageDraw.Draw(image)

    def center(value, y, size=24, color='#111111', width=590, x=320):
        font = ImageFont.truetype(str(FONT), size=size)
        value = str(value)
        while draw.textlength(value, font=font) > width and size > 10:
            size -= 1
            font = ImageFont.truetype(str(FONT), size=size)
        while draw.textlength(value, font=font) > width and len(value) > 1:
            value = value[:-2] + '…'
        draw.text((x,y),value,fill=color,font=font,anchor='mt')

    if side == 'front':
        with Image.open(ASSETS/'pgpc-header.png') as header:
            image.paste(header.resize((WIDTH,204),Image.Resampling.LANCZOS),(0,0))
        # Only the unobstructed building crop is reused; the person's photo area is rebuilt.
        with Image.open(ASSETS/'pgpc-campus.png') as campus:
            background = campus.resize((490,590), Image.Resampling.LANCZOS)
            image.paste(background,(145,220))
        draw.rectangle((205,390,580,760), fill='#d6deeb')
        draw.ellipse((330,433,455,558),fill='#8e9bb8')
        draw.rounded_rectangle((266,585,520,740),radius=75,fill='#8e9bb8')
        center('PHOTO PLACEHOLDER',743,14,BLUE,width=440,x=392)
        course = str(student['course'])
        lines = COURSES.get(course.upper(), (course, 'STUDENT'))
        vertical = Image.new('RGBA',(600,126),(0,0,0,0))
        vd=ImageDraw.Draw(vertical)
        for index,line in enumerate(lines):
            font=ImageFont.truetype(str(FONT),size=36)
            while vd.textlength(line,font=font)>590:
                font=ImageFont.truetype(str(FONT),size=font.size-1)
            vd.text((300,index*56),line,font=font,fill='white',anchor='mt')
        vertical=vertical.rotate(90,expand=True)
        image.paste(vertical,(7,235),vertical)
        # Curved blue footer with the same yellow divider as the reference layout.
        draw.polygon([(0,807),(140,809),(270,791),(410,778),(535,771),(640,789),(640,1000),(0,1000)],fill=GOLD)
        draw.polygon([(0,819),(140,821),(270,803),(410,790),(535,783),(640,801),(640,1000),(0,1000)],fill=BLUE)
        center('SIGNATURE PLACEHOLDER',865,16,'#cbd0eb')
        center(student['name'],904,31,'white',width=590)
        draw.line((125,943,607,943),fill='white',width=3)
        center(f'STUDENT NO. {student["student_id"]}',958,26,GOLD)
    else:
        center('Person to notify in case of emergency:',40,25)
        center('[EMERGENCY CONTACT NAME]',93,27)
        center('[CONTACT NUMBER]',143,25)
        center('[ADDRESS]',193,24)
        center('IMPORTANT REMINDER',241,29)
        font=ImageFont.truetype(str(FONT),size=19)
        reminders=[
            ['This card is non-transferable and valid only during', 'the term indicated.'],
            ['It must be worn conspicuously at all times inside', 'the campus.'],
            ['The student cardholder is duly bound to obey', 'College policies, rules and regulations.'],
            ['In case of loss, the finder is requested to return this', "to the REGISTRAR’S OFFICE of", 'Padre Garcia Polytechnic College.'],
        ]
        y=285
        for lines in reminders:
            draw.ellipse((29,y+8,35,y+14),fill='#111111')
            for line in lines:
                draw.text((51,y),line,font=font,fill='#111111')
                y+=25
            y+=12
        if qr_url:
            code=qrcode.QRCode(box_size=4,border=4,error_correction=qrcode.constants.ERROR_CORRECT_M)
            code.add_data(qr_url);code.make(fit=True)
            qr=code.make_image(fill_color='black',back_color='white').convert('RGB')
            # Keep whole modules at integer pixel widths to preserve scanability.
            if qr.width > 170:
                code.box_size=max(1,170//(len(code.get_matrix())))
                qr=code.make_image(fill_color='black',back_color='white').convert('RGB')
            image.paste(qr,((WIDTH-qr.width)//2,565+(170-qr.height)//2))
        else:
            draw.rectangle((245,574,395,724),outline='#777777',width=2)
            center('QR CODE',630,20)
        center('CONTACT US: [OFFICE NUMBER]',746,22)
        center('FB PAGE: Padre Garcia Polytechnic College',789,22)
        center('[AUTHORIZED SIGNATURE]',841,18,'#666666')
        draw.line((126,886,514,886),fill='#111111',width=2)
        center('Municipal Mayor / Chairperson,',902,20)
        center('PGPC Board of Trustees',929,20)
        draw.rounded_rectangle((179,960,461,994),radius=4,outline='#111111',width=2)
        center('A.Y. 2026 – 2027',965,23)
    # Retain the prototype's sample indication without replacing the reference layout.
    watermark=Image.new('RGBA',image.size,(0,0,0,0))
    wd=ImageDraw.Draw(watermark)
    wd.text((320,530),'SAMPLE',anchor='mm',font=ImageFont.truetype(str(FONT),64),fill=(255,255,255,125) if side=='front' else (80,80,80,40))
    image=Image.alpha_composite(image.convert('RGBA'),watermark).convert('RGB')
    return image
