import re
import os
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Preformatted, Image
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Read the README.md file
with open('README.md', 'r', encoding='utf-8') as f:
    content = f.read()

# Create PDF
pdf_filename = 'README.pdf'
doc = SimpleDocTemplate(pdf_filename, pagesize=letter, topMargin=0.75*inch, bottomMargin=0.75*inch)

# Get base styles
styles = getSampleStyleSheet()

# Create custom styles
title_style = ParagraphStyle(
    'CustomTitle',
    parent=styles['Heading1'],
    fontSize=24,
    textColor='#1f2937',
    spaceAfter=12,
    alignment=TA_LEFT,
    fontName='Helvetica-Bold'
)

heading2_style = ParagraphStyle(
    'CustomHeading2',
    parent=styles['Heading2'],
    fontSize=16,
    textColor='#1f2937',
    spaceAfter=10,
    spaceBefore=12,
    alignment=TA_LEFT,
    fontName='Helvetica-Bold'
)

heading3_style = ParagraphStyle(
    'CustomHeading3',
    parent=styles['Heading3'],
    fontSize=13,
    textColor='#1f2937',
    spaceAfter=8,
    spaceBefore=8,
    alignment=TA_LEFT,
    fontName='Helvetica-Bold'
)

body_style = ParagraphStyle(
    'CustomBody',
    parent=styles['BodyText'],
    fontSize=10,
    leading=12,
    alignment=TA_JUSTIFY,
    spaceAfter=8
)

code_style = ParagraphStyle(
    'CustomCode',
    parent=styles['Normal'],
    fontSize=8,
    fontName='Courier',
    leftIndent=20,
    rightIndent=20,
    spaceAfter=8,
    spaceBefore=8,
    backColor='#f3f4f6'
)

list_style = ParagraphStyle(
    'CustomList',
    parent=styles['Normal'],
    fontSize=10,
    leading=12,
    leftIndent=30,
    spaceAfter=4,
    bulletIndent=20
)

# Process content
elements = []
lines = content.split('\n')
in_code_block = False
code_block_lines = []

for line in lines:
    stripped = line.strip()
    
    # Handle code blocks
    if stripped.startswith('```'):
        if in_code_block:
            # End of code block
            code_text = '\n'.join(code_block_lines)
            elements.append(Preformatted(code_text, code_style))
            elements.append(Spacer(1, 0.1*inch))
            in_code_block = False
            code_block_lines = []
        else:
            # Start of code block
            in_code_block = True
            code_block_lines = []
        continue
    
    if in_code_block:
        code_block_lines.append(line)
        continue
    
    # Handle headings
    if stripped.startswith('# '):
        title = stripped[2:].strip()
        elements.append(Paragraph(title, title_style))
        elements.append(Spacer(1, 0.2*inch))
    elif stripped.startswith('## '):
        heading = stripped[3:].strip()
        elements.append(Paragraph(heading, heading2_style))
        elements.append(Spacer(1, 0.1*inch))
    elif stripped.startswith('### '):
        heading = stripped[4:].strip()
        elements.append(Paragraph(heading, heading3_style))
        elements.append(Spacer(1, 0.05*inch))
    elif stripped.startswith('- '):
        # List item
        item_text = stripped[2:].strip()
        # Remove markdown formatting
        item_text = re.sub(r'\[([^\]]+)\]\([^\)]*\)', r'\1', item_text)
        item_text = re.sub(r'`([^`]+)`', r'<i>\1</i>', item_text)
        item_text = re.sub(r'\*\*([^\*]+)\*\*', r'<b>\1</b>', item_text)
        elements.append(Paragraph('• ' + item_text, list_style))
    elif stripped:
        # Regular paragraph
        text = stripped
        # Remove markdown formatting
        text = re.sub(r'\[([^\]]+)\]\([^\)]*\)', r'\1', text)
        text = re.sub(r'`([^`]+)`', r'<i>\1</i>', text)
        text = re.sub(r'\*\*([^\*]+)\*\*', r'<b>\1</b>', text)
        text = re.sub(r'_([^_]+)_', r'<i>\1</i>', text)
        
        elements.append(Paragraph(text, body_style))
        elements.append(Spacer(1, 0.05*inch))
    else:
        # Empty line
        elements.append(Spacer(1, 0.05*inch))

# Build PDF
doc.build(elements)

print(f"✓ PDF created successfully: {pdf_filename}")
if os.path.exists(pdf_filename):
    file_size = os.path.getsize(pdf_filename) / 1024
    print(f"✓ File size: {file_size:.2f} KB")
    print(f"✓ Location: {os.path.abspath(pdf_filename)}")
else:
    print("✗ Failed to create PDF")
