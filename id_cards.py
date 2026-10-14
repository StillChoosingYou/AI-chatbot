"""Original sample card artwork; never uses reference photos or personal details."""
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 640, 1000
NAVY = '#182a57'
GOLD = '#f1cc53'
INK = '#223252'


def sample_card(student, side):
    image = Image.new('RGB', (WIDTH, HEIGHT), '#eef1f7')
    draw = ImageDraw.Draw(image)

    def text(value, y, size=24, color=INK, left=40, width=560):
        font = ImageFont.load_default(size=size)
        value = str(value)
        while draw.textlength(value, font=font) > width and len(value) > 1:
            value = value[:-2] + '…'
        draw.text((left, y), value, fill=color, font=font)

    draw.rectangle((0, 0, WIDTH, 145), fill=NAVY)
    text('SCHOOL PROPERTY', 32, 32, GOLD)
    text('MANAGEMENT', 76, 32, '#ffffff')
    draw.polygon([(0,145),(WIDTH,145),(WIDTH,162),(0,188)], fill=GOLD)
    if side == 'front':
        text('STUDENT IDENTIFICATION', 215, 24)
        draw.rounded_rectangle((140,270,500,610), radius=20, fill='#dce3ef')
        # A neutral silhouette drawn from shapes, not a real face.
        draw.ellipse((267,330,373,436), fill='#a6b4cf')
        draw.rounded_rectangle((215,458,425,575), radius=50, fill='#a6b4cf')
        text('PHOTO PLACEHOLDER', 630, 18, '#7a87a3', left=195, width=410)
        text(student['name'], 690, 32)
        text(student['course'], 745, 26)
        text(f'Section {student["section"]}  |  Year {student["year_level"]}', 792, 22)
        text(f'Student ID: {student["student_id"]}', 843, 20)
        text('Signature not provided', 893, 18, '#7a87a3')
    else:
        text('EMERGENCY CONTACT', 225, 25)
        text('Name: Not provided', 279, 22)
        text('Contact number: Not provided', 325, 22)
        text('Address: Not provided', 371, 22)
        draw.line((40,433,600,433), fill='#ccd4e5', width=2)
        text('ID INFORMATION', 477, 25)
        for value, y in [('For student identification.',535),
                         ('Contact the school office for assistance.',581),
                         ('Report a missing card to authorized staff.',627)]:
            text(value,y,21)
        draw.rounded_rectangle((40,710,600,890), radius=15, fill='#e1e6f0')
        text('SAMPLE BACK OF ID', 746, 27, left=90, width=470)
        text('Replace with an approved ID image.', 800, 22, left=90, width=470)
        text('No contact or signature is included.', 842, 20, left=90, width=470)
    draw.rectangle((0,945,WIDTH,HEIGHT), fill=NAVY)
    text('SAMPLE • NOT AN OFFICIAL ID', 961, 20, GOLD, left=140, width=480)
    return image
