"""Local raster/geometry measurements only. Never a live vision verdict or acceptance writer."""
import argparse
import hashlib
import io
import json
import re
from pathlib import Path
import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright


def luminance(rgb):
    x=np.asarray(rgb,dtype=float)/255
    x=np.where(x<=.04045,x/12.92,((x+.055)/1.055)**2.4)
    return x @ np.array([.2126,.7152,.0722])


def measure(directory):
    directory=Path(directory).resolve();png=directory/'poster.png'; image=np.array(Image.open(png).convert('RGB')).astype(float)
    with sync_playwright() as p:
        b=p.chromium.launch();page=b.new_page(viewport={'width':1080,'height':1350},device_scale_factor=2)
        external=[]
        def route(r):
            if r.request.url.startswith(('http:','https:')): external.append(r.request.url);r.abort()
            else:r.continue_()
        page.route('**/*',route)
        page.set_content((directory/'poster.html').read_text());page.evaluate('document.fonts.ready')
        reference=np.array(Image.open(io.BytesIO(page.screenshot())).convert('RGB'))
        assert np.array_equal(image,reference),'Measurements must describe the supplied PNG exactly'
        boxes=page.evaluate('''() => Object.fromEntries(['.brand-logo','.headline','.emphasis','.sub','.cta','.cta-text'].map(s=>{
          const e=document.querySelector(s),r=e.getBoundingClientRect(),c=getComputedStyle(e);
          return[s,{x:r.x,y:r.y,width:r.width,height:r.height,font_px:parseFloat(c.fontSize),color:c.color,dir:c.direction,text:e.innerText||'',scrollWidth:e.scrollWidth,clientWidth:e.clientWidth}];}))''')
        bidi=page.locator('.cta-text bdi').bounding_box()
        page.add_style_tag(content='.headline,.sub,.cta-text{visibility:hidden!important}')
        underlay=np.array(Image.open(io.BytesIO(page.screenshot())).convert('RGB')).astype(float)
        b.close()
    assert external==[], 'No remote resources allowed'
    primary=['.brand-logo','.headline','.sub','.cta']
    for selector in primary:
        q=boxes[selector]
        assert q['x']>=48 and q['y']>=40 and q['x']+q['width']<=1032 and q['y']+q['height']<=1310, selector
        assert q['scrollWidth']<=q['clientWidth']+1, selector
    for i,a in enumerate(primary):
        for bb in primary[i+1:]:
            q,r=boxes[a],boxes[bb]
            assert (q['x']+q['width']<=r['x'] or r['x']+r['width']<=q['x'] or
                    q['y']+q['height']<=r['y'] or r['y']+r['height']<=q['y']), (a,bb)
    contrast={}
    for selector in ['.headline','.emphasis','.sub','.cta-text']:
        q=boxes[selector]; ink=np.array([int(v) for v in re.findall(r'\d+',q['color'])[:3]])
        x,y=int(q['x']*2),int(q['y']*2);right,bottom=int((q['x']+q['width'])*2),int((q['y']+q['height'])*2)
        actual=image[y:bottom,x:right];bg=underlay[y:bottom,x:right]
        mask=(np.max(np.abs(actual-ink),axis=2)<14)&(np.max(np.abs(actual-bg),axis=2)>20)
        assert mask.sum()>100,selector
        a,bg_l=luminance(actual[mask]),luminance(bg[mask]);ratios=(np.maximum(a,bg_l)+.05)/(np.minimum(a,bg_l)+.05)
        contrast[selector]={'core_glyph_pixels':int(mask.sum()),'min_ratio':round(float(ratios.min()),2),
                           'p05_ratio':round(float(np.percentile(ratios,5)),2), 'method':'PNG glyph interiors vs same-render hidden-text underlay; antialiased edges excluded'}
        assert ratios.min()>=4.5,selector
    assert boxes['.sub']['font_px']*390/1080>=16
    assert boxes['.cta-text']['text']=='نزِّل Firefox'
    assert bidi['x']+bidi['width']<boxes['.cta-text']['x']+boxes['.cta-text']['width']
    result={'kind':'LOCAL_RASTER_AND_GEOMETRY_MEASUREMENTS','canonical_accepted':False,'live_vision_qa':False,'human_review':False,
       'sha256':hashlib.sha256(png.read_bytes()).hexdigest(),'raster_shape':list(image.shape),
       'supplied_png_matches_renderer_pixels':True,'external_requests':external,'boxes_css_px':boxes,'contrast':contrast,
       'mobile_390_font_px':{s:round(boxes[s]['font_px']*390/1080,2) for s in ['.headline','.sub','.cta-text']},
       'checks':{'rtl_bidi':True,'no_box_overlap_or_frame_clipping':True,'safe_margins':True,'text_region_contrast':True},
       'limitations':['Glyph-interior contrast is a local measurement, not complete visibility/semantic certification.',
         '390px PNG was separately viewed by AI; no paid QA or human approval is implied.']}
    (directory/'local-checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path)
    print(json.dumps(measure(parser.parse_args().directory),ensure_ascii=False,indent=2))
