import os
from io import BytesIO
import datetime

try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import landscape, A4
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False


def number_to_words(n):
    if n == 0:
        return "Zero"
    
    ones = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
            "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]
    
    def convert(num):
        if num < 20:
            return ones[num]
        if num < 100:
            return tens[num // 10] + (" " + ones[num % 10] if num % 10 != 0 else "")
        if num < 1000:
            return ones[num // 100] + " Hundred" + (" and " + convert(num % 100) if num % 100 != 0 else "")
        if num < 100000:
            return convert(num // 1000) + " Thousand" + (" " + convert(num % 1000) if num % 1000 != 0 else "")
        if num < 10000000:
            return convert(num // 100000) + " Lakh" + (" " + convert(num % 100000) if num % 100000 != 0 else "")
        return convert(num // 10000000) + " Crore" + (" " + convert(num % 10000000) if num % 10000000 != 0 else "")

    return convert(int(n)) + " Rupee only"


def generate_receipt_pdf(receipt, society):
    if not REPORTLAB_AVAILABLE:
        raise ImportError("reportlab is not installed. Run: pip install reportlab")

    buffer = BytesIO()
    # Using A5 Landscape to match receipt ratio better
    width, height = 595.27, 420.94 # A5 Landscape (half of A4)
    c = canvas.Canvas(buffer, pagesize=(width, height))
    
    orange = HexColor("#D1561D")
    dark_gray = HexColor("#333333")
    
    # ── Borders ──
    c.setStrokeColor(orange)
    c.setLineWidth(1.5)
    c.rect(15, 15, width-30, height-30)
    c.setLineWidth(0.5)
    c.rect(19, 19, width-38, height-38)
    
    # ── Top Left: Society Box ──
    # Box
    box_x = 35
    box_y = height - 100
    box_w = 200
    box_h = 60
    c.setLineWidth(1)
    c.rect(box_x, box_y, box_w, box_h)
    
    c.setFillColor(dark_gray)
    
    soc_name = str(society.get('society_name', 'Society Name')).upper()
    block = str(society.get('block', '')).strip()
    if block and block != '-':
        soc_name = f"{soc_name} - {block.upper()}"
        
    name_font_size = 12
    if c.stringWidth(soc_name, "Helvetica-Bold", name_font_size) > box_w - 10:
        name_font_size = 10
        if c.stringWidth(soc_name, "Helvetica-Bold", name_font_size) > box_w - 10:
            name_font_size = 8
            
    c.setFont("Helvetica-Bold", name_font_size)
    c.drawCentredString(box_x + box_w/2, box_y + 40, soc_name)
    
    c.setFont("Helvetica", 9)
    address = society.get('address', '')
    from reportlab.lib.utils import simpleSplit
    lines = simpleSplit(address, "Helvetica", 9, box_w - 10)
    y_offset = 22 if len(lines) > 1 else 16
    for line in lines[:2]:  # at most 2 lines
        c.drawCentredString(box_x + box_w/2, box_y + y_offset, line)
        y_offset -= 12
        
    # ── Top Right: Year & Date ──
    right_col = width - 180
    c.setFont("Helvetica", 11)
    
    # Year / Duration Box
    c.drawString(right_col - 40, height - 55, "Year:")
    c.setStrokeColor(orange)
    c.rect(right_col, height - 60, 130, 20)
    
    duration_mode = receipt.get('duration_mode', '')
    details = receipt.get('duration_details', {})
    year_val = details.get('year', '')
    if year_val and '-' in str(year_val):
        # shorten 2026-2027 to 2026-27
        parts = str(year_val).split('-')
        if len(parts) == 2 and len(parts[1]) == 4:
            year_val = f"{parts[0]}-{parts[1][2:]}"

    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    display_str = ""

    if duration_mode == 'Monthly':
        months = details.get('months', [])
        m_str = ", ".join([month_names[int(m)-1] for m in months if str(m).isdigit() and 1 <= int(m) <= 12])
        display_str = f"{m_str} {year_val}"
    elif duration_mode in ['Quarterly', 'Six-Month']:
        dtype = details.get('type', '')
        if dtype == 'Custom':
            months = details.get('months', [])
            m_str = ", ".join([month_names[int(m)-1] for m in months if str(m).isdigit() and 1 <= int(m) <= 12])
            display_str = f"{m_str} {year_val}"
        else:
            fy_start = int(society.get('financial_year_start_month', 4))
            start_offset = 0
            count = 0
            if dtype == 'Q1': start_offset, count = 0, 3
            elif dtype == 'Q2': start_offset, count = 3, 3
            elif dtype == 'Q3': start_offset, count = 6, 3
            elif dtype == 'Q4': start_offset, count = 9, 3
            elif dtype == 'H1': start_offset, count = 0, 6
            elif dtype == 'H2': start_offset, count = 6, 6
            
            if count > 0:
                m1 = (fy_start - 1 + start_offset) % 12
                m2 = (fy_start - 1 + start_offset + count - 1) % 12
                display_str = f"{month_names[m1]}-{month_names[m2]} {year_val}"
            else:
                display_str = f"{dtype} {year_val}"
    elif duration_mode == 'Yearly':
        fy_start = int(society.get('financial_year_start_month', 4))
        fy_m = month_names[fy_start - 1]
        display_str = f"{fy_m} {year_val}"
    else:
        display_str = f"{duration_mode} {year_val}"
        
    display_str = display_str.strip()
    if len(display_str) > 16:
        c.setFont("Helvetica", 9)
    else:
        c.setFont("Helvetica", 11)
        
    c.drawCentredString(right_col + 65, height - 54, display_str)
    
    # Date
    c.drawString(right_col - 40, height - 85, "Date:")
    c.rect(right_col, height - 90, 130, 20)
    
    dt_str = receipt.get('payment_date', '')
    date_display = ""
    if dt_str:
        try:
            date_display = datetime.datetime.strptime(dt_str, "%Y-%m-%d").strftime("%d/%m/%Y")
        except:
            date_display = dt_str
    c.drawCentredString(right_col + 65, height - 84, date_display)
    
    # ── Body Text ──
    text_y = height - 150
    left_margin = 35
    
    c.setFont("Helvetica-Oblique", 12)
    c.drawString(left_margin, text_y, "Received with thanks from:")
    c.setFont("Helvetica-BoldOblique", 12)
    
    name_str = f" Mr. {receipt.get('member_name', '')} (Flat no: {receipt.get('flat_number', '')})"
    c.drawString(left_margin + 160, text_y, name_str)
    c.setStrokeColor(dark_gray)
    c.line(left_margin + 160, text_y - 2, width - 40, text_y - 2)
    
    text_y -= 30
    c.setFont("Helvetica-Oblique", 12)
    c.drawString(left_margin, text_y, "the sum of Rupees")
    
    amount = float(receipt.get('amount', 0))
    amt_words = number_to_words(amount)
    c.setFont("Helvetica-BoldOblique", 12)
    c.drawString(left_margin + 110, text_y, amt_words)
    c.line(left_margin + 110, text_y - 2, width - 40, text_y - 2)
    
    # Payment modes with strikethrough
    text_y -= 40
    c.line(left_margin, text_y - 2, width - 240, text_y - 2)
    
    c.setFont("Helvetica-Oblique", 11)
    pmode_start = width - 230
    c.drawString(pmode_start, text_y, "by ")
    
    p_mode = receipt.get('payment_mode', '').lower()
    mode_width = c.stringWidth(p_mode, "Helvetica-Oblique", 11)
    c.drawString(pmode_start + 18, text_y, p_mode)
    c.setStrokeColor(dark_gray)
    c.setLineWidth(1)
    c.line(pmode_start + 18, text_y - 2, pmode_start + 18 + mode_width, text_y - 2)
    c.drawString(pmode_start + 18 + mode_width, text_y, " in full / advance")
        
    text_y -= 30
    c.setFont("Helvetica-Oblique", 11)
    c.drawString(left_margin, text_y, "payment of our Bill No.")
    
    c.line(left_margin + 125, text_y - 2, left_margin + 200, text_y - 2)
    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(left_margin + 162.5, text_y, str(receipt.get('receipt_no', '')))
    
    c.setFont("Helvetica-Oblique", 11)
    c.drawString(left_margin + 205, text_y, "Dated")
    c.setFont("Helvetica-Bold", 11)
    c.drawString(left_margin + 245, text_y, date_display)
    c.line(left_margin + 245, text_y - 2, left_margin + 320, text_y - 2)
    
    text_y -= 25
    
    # ── Bottom Left: Amount Box ──
    text_y -= 50
    c.setStrokeColor(orange)
    c.setLineWidth(1)
    c.rect(left_margin + 35, text_y - 5, 120, 25)
    
    c.setFont("Helvetica", 14)
    c.setFillColor(orange)
    c.drawString(left_margin + 10, text_y + 2, "Rs")
    c.setFillColor(dark_gray)
    c.drawCentredString(left_margin + 95, text_y + 2, f"{amount}/-")
    
    # ── Bottom Left Text ──
    c.setFont("Helvetica-Oblique", 10)
    c.drawString(left_margin, text_y - 30, "This receipt is valid subject to Realisation of cheque.")
    
    # ── Bottom Right: Signature ──
    c.setFont("Helvetica-Oblique", 10)
    c.setFillColor(dark_gray)
    c.drawCentredString(width - 90, text_y - 15, "Authorized Signatory")
    
    # Signature rendering
    signature_image = society.get('signature_image', '')
    if signature_image and signature_image.startswith('data:image'):
        try:
            import base64
            from reportlab.lib.utils import ImageReader
            from PIL import Image
            
            b64_data = signature_image.split(",")[1]
            image_data = base64.b64decode(b64_data)
            
            # Open with PIL and downscale to prevent huge PDFs & slow rendering
            pil_img = Image.open(BytesIO(image_data))
            if pil_img.width > 600 or pil_img.height > 300:
                # Use ANTIALIAS for older Pillow compatibility, or LANCZOS for newer
                resample_filter = getattr(Image, 'Resampling', Image).LANCZOS
                pil_img.thumbnail((600, 300), resample_filter)
                
            img = ImageReader(pil_img)
            
            # Draw image larger (180x80 instead of 140x60)
            c.drawImage(img, width - 180, text_y - 15, 160, 80, preserveAspectRatio=True, anchor='s', mask='auto')
        except Exception:
            # Fallback to blue scribble if decoding fails
            c.setStrokeColor(HexColor("#000080"))
            c.setLineWidth(1.5)
            sx = width - 120
            sy = text_y + 5
            c.bezier(sx, sy, sx+10, sy+15, sx+20, sy-10, sx+30, sy+10)
            c.bezier(sx+30, sy+10, sx+40, sy+20, sx+50, sy-5, sx+60, sy+15)
    else:
        # Default Blue scribble representation for signature
        c.setStrokeColor(HexColor("#000080"))
        c.setLineWidth(1.5)
        sx = width - 120
        sy = text_y + 5
        c.bezier(sx, sy, sx+10, sy+15, sx+20, sy-10, sx+30, sy+10)
        c.bezier(sx+30, sy+10, sx+40, sy+20, sx+50, sy-5, sx+60, sy+15)
    
    c.save()
    buffer.seek(0)
    return buffer
