#!/usr/bin/env python
 
#        +-----------------------------------------------------------------------------+
#        | GPL                                                                         |
#        +-----------------------------------------------------------------------------+
#        | Copyright (c) Brett Smith <tanktarta@blueyonder.co.uk>                      |
#        |                                                                             |
#        | This program is free software; you can redistribute it and/or               |
#        | modify it under the terms of the GNU General Public License                 |
#        | as published by the Free Software Foundation; either version 2              |
#        | of the License, or (at your option) any later version.                      |
#        |                                                                             |
#        | This program is distributed in the hope that it will be useful,             |
#        | but WITHOUT ANY WARRANTY; without even the implied warranty of              |
#        | MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the               |
#        | GNU General Public License for more details.                                |
#        |                                                                             |
#        | You should have received a copy of the GNU General Public License           |
#        | along with this program; if not, write to the Free Software                 |
#        | Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA. |
#        +-----------------------------------------------------------------------------+
 
import os
import cairo
import rsvg
import sys
import traceback
import pango
import pangocairo
import g15_driver as g15driver
import g15_util as g15util
import g15_gtk as g15gtk
import xml.sax.saxutils as saxutils
import base64
from string import Template
from copy import deepcopy
from cStringIO import StringIO

from lxml import etree

BASE_PX=18.0

class TextBox():
    def __init__(self):
        self.bounds = ( )
        self.text = "" 
        self.css = { }
        self.transforms = []
        
class Component():
        
    def __init__(self, id):
        self.id = id
        self.theme = None
        
    def configure(self, theme):
        self.theme = theme
        self.on_configure()
        
    def on_configure(self):
        raise Exception("Not implemented.")
        
    def draw(self, canvas, element, properties, attributes):
        raise Exception("Not implemented.")

class Scrollbar(Component):
    
    def __init__(self, id, values_callback):
        Component.__init__(self, id)
        self.values_callback = values_callback
        
    def on_configure(self):
        pass
        
    def draw(self, canvas, element, properties, attributes):        
        if self.theme != None:
            max, view_size, position = self.values_callback(properties, attributes)
            knob = element.xpath('//svg:*[@class=\'knob\']',namespaces=self.theme.nsmap)[0]
            track = element.xpath('//svg:*[@class=\'track\']',namespaces=self.theme.nsmap)[0]
            track_bounds = g15util.get_bounds(track)
            knob_bounds = g15util.get_bounds(knob)
            scale = max / view_size
            knob.set("y", str( int( knob_bounds[1] + ( position / scale ) ) ) )
            knob.set("height", str(int(track_bounds[3] / scale )))
        
class Menu(Component):
    def __init__(self, id):
        Component.__init__(self, id)
        self.items = []
        self.base = 0
        self.selected = None
        
    def on_configure(self):
        self.view_element = self.theme.get_element(self.id)
        if self.view_element == None:
            raise Exception("No element in SVG with ID of %s. Required for Menu component" % self.id)
        self.view_bounds  = g15util.get_actual_bounds(self.view_element)
        self.entry_theme = G15Theme(self.theme.dir, self.theme.screen, "menu-entry")
        
    def get_scroll_values(self, properties, attributes):
        max = 0
        for item in self.items:
            max += self.get_item_height(item, True)
        return max, self.view_bounds[3], self.base
        
    def get_item_height(self, item, group = False):
        return self.entry_theme.bounds[3]
    
    
    def draw(self, canvas, element, properties, attributes):
        # Get the Y position of the selected item
        y = 0 
        selected_y = -1
        for item in self.items:
            ih = self.get_item_height(item, True)
            if item == self.selected:
                selected_y = y
            y += ih
                
        new_base = self.base
                
        # How much vertical space there is
        v_space = self.view_bounds[3]
            
        # If the position of the selected item is offscreen below, change the offset so it is just visible
        if self.selected != None:
            ih = self.get_item_height(self.selected, True)
            if selected_y >= new_base + v_space - ih:
                new_base = ( selected_y + ih ) - v_space
            # If the position of the selected item is offscreen above base, change the offset so it is just visible
            elif selected_y < new_base:
                new_base = selected_y
                
        if new_base != self.base:
            if new_base < self.base:
                self.base -=  max(1, ( self.base - new_base ) / 10)
            else:
                self.base += max(1, ( new_base - self.base ) / 10)
            g15util.schedule("ScrollTo", 0.05, self.theme.screen.redraw)
        
        canvas.save()
        
        # Clip to the max size of the viewport        
        canvas.rectangle(self.view_bounds[0], self.view_bounds[1], self.view_bounds[2], self.view_bounds[3])
        canvas.clip() 
        
        # Move to the base
        canvas.translate(self.view_bounds[0], -self.base + self.view_bounds[1])
        
        # This will handle two levels of menus
        y = -self.base
        for item in self.items:
            y = self._do_item(item, self.selected, canvas, y, properties, attributes,  True)
                
        canvas.restore() 
        
    def _do_item(self, item, selected, canvas, y, properties, attributes, group = False):        
        # Don't draw items that are not visible
        ih = self.get_item_height(item, group)
        if y >= -ih and y <= self.view_bounds[3] + ih:
            self.render_item(item, selected, canvas, properties, attributes, group)
        canvas.translate(0, ih)
        y += ih
        return y
        
    def render_item(self, item, selected, canvas, properties, attributes, group = False):
        raise Exception("Not implemented.")

class G15Theme:
    
    def __init__(self, dir, screen, variant = None):
        self.screen = screen
        self.driver = screen.driver
        self.svg_processor = None
        self.dir = dir
        self.variant = variant
        self.theme_name = os.path.basename(dir)
        self.plugin_name = os.path.basename(os.path.dirname(dir))
        self.offscreen_windows = []
        self.components = {}
        
        module_name = self.get_path_for_variant(dir, variant, "py", fatal = False, prefix = self.plugin_name.replace("-","_") + "_" + self.theme_name + "_")
        module = None
        self.instance = None
        if module_name != None:
            if not dir in sys.path:
                sys.path.insert(0, dir)
            module = __import__(os.path.basename(module_name)[:-3])
            self.instance = module.Theme(self.screen, self)
            
        self.nsmap = {
            'sodipodi': 'http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd',
            'cc': 'http://web.resource.org/cc/',
            'svg': 'http://www.w3.org/2000/svg',
            'dc': 'http://purl.org/dc/elements/1.1/',
            'xlink': 'http://www.w3.org/1999/xlink',
            'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
            'inkscape': 'http://www.inkscape.org/namespaces/inkscape'
            }
        
        # TODO stop parsing everytime, take a copy
        path = self.get_path_for_variant(self.dir, self.variant, "svg")
        self.document = etree.parse(path)
        self.driver.process_svg(self.document)
        self.bounds = g15util.get_bounds(self.document.getroot())

    def get_path_for_variant(self, dir, variant, extension, fatal = True, prefix = ""):
        if variant == None:
            variant = ""
        elif variant != "":
            variant = "-" + variant
        path = os.path.join(dir, prefix + self.driver.get_model_name() + variant + "." + extension )
        if not os.path.exists(path):
            path = os.path.join(dir, prefix + "default" + variant + "." + extension)
            if not os.path.exists(path):
                if fatal:
                    raise Exception("No .%s file for model %s in %s for variant %s" % ( extension, self.driver.get_model_name(), dir, variant ))
                else:
                    return None
        return path
    
    def convert_css_size(self, css_size):
        em = 1.0
        if css_size.endswith("px"):
            # Get EM based on size of 10px (the default cairo context is 10 so this should be right?)
            px = float(css_size[:len(css_size) - 2])          
            em = px / BASE_PX
        elif css_size.endswith("pt"):      
            # Convert to px first, then use same algorithm
            pt = float(css_size[:len(css_size) - 2])
            px = ( pt * 96.0 ) / 72.0          
            em = px / BASE_PX
        elif css_size.endswith("%"):
            em = float(css_size[:len(css_size) - 1]) / 100.0
        elif css_size.endswith("em"):
            em = float(css_size)
        else:
            raise Exception("Unknown font size")
        return em
        
    def get_string_width(self, text, canvas, css):        
        # Font family
        font_family = css.get("font-family")
        
        # Font size (translate to 'em')
        font_size_text = css.get("font-size")
        em = self.convert_css_size(font_size_text)
        
        # Font weight
        font_weight = cairo.FONT_WEIGHT_NORMAL
        if css.get("font-weight") == "bold":
            font_weight = cairo.FONT_WEIGHT_BOLD
        
        # Font style
        font_slant = cairo.FONT_SLANT_NORMAL
        if css.get("font-style") == "italic":
            font_slant = cairo.FONT_SLANT_ITALIC
        elif css.get("font-style") == "oblique":
            font_slant = cairo.FONT_SLANT_OBLIQUE
        
        try :
            canvas.save()
            canvas.select_font_face(font_family, font_slant, font_weight)
            canvas.set_font_size(em * 10.0 * ( 4 / 3) )  
            return canvas.text_extents(text)[:4]
        finally:            
            canvas.restore()
            
    def parse_css(self, styles_text):        
        # Parse CSS styles            
        styles = { }
        for style in styles_text.split(";") :
            style_args = style.lstrip().rstrip().split(":")
            if len(style_args) > 1:
                styles[style_args[0].rstrip()] = style_args[1].lstrip().rstrip()
            else:
                print "WARNING: Malformed CSS style %s." % style
        return styles
    
    def format_styles(self, styles):
        buf = ""
        for style in styles:
            buf += style + ":" + styles[style] + ";"
        return buf
    
    def add_component(self, component):
        component.configure(self)
        self.components[component.id] = component

    def add_window(self, id, page):
        # Get the bounds of the GTK element and remove it from the SVG
        element = self.document.getroot().xpath('//svg:*[@id=\'%s\']' % id,namespaces=self.nsmap)[0]
        offscreen_bounds = g15util.get_actual_bounds(element)
        element.getparent().remove(element)
        window = g15gtk.G15Window(self.screen, page, offscreen_bounds[0], offscreen_bounds[1], offscreen_bounds[2], offscreen_bounds[3])
        self.offscreen_windows.append((window, offscreen_bounds))
        return window
    
    def get_element(self, id, root = None):
        if root == None:
            root = self.document.getroot()
        els = root.xpath('//svg:*[@id=\'%s\']' % str(id),namespaces=self.nsmap)
        return els[0] if len(els) > 0 else None
            
    def draw(self, canvas, properties = {}, attributes = {}):
        # TODO tidy this lot up
        
        properties = dict(properties)
        document = deepcopy(self.document)
        processing_result = None
        
        # Give the python portion of the theme chance to draw stuff under the SVG
        if self.instance != None:            
            try :
                getattr(self.instance, "paint_background")
                try :
                    self.instance.paint_background(properties, attributes)
                except:
                    traceback.print_exc(file=sys.stderr)
            except AttributeError:                
                # Doesn't exist
                pass
            
        root = document.getroot()
            
        # Remove all elements that are dependent on properties having non blank values
        for element in root.xpath('//svg:*[@title]',namespaces=self.nsmap):
            title = element.get("title")
            if title != None:
                args = title.split(" ")
                if args[0] == "del":
                    var = args[1]
                    set = True
                    if var.startswith("!"):
                        var = var[1:]
                        set = False
                    if ( set and var in properties and properties[var] != "" and properties[var] != False ) or ( not set and ( not var in properties or properties[var] == "" or properties[var] == False ) ):
                        element.getparent().remove(element)
            
        # Process any components
        for component_id in self.components.keys():
            component_elements = root.xpath('//svg:*[@id=\'%s\']' % component_id,namespaces=self.nsmap)
            if len(component_elements) > 0:
                self.components[component_id].draw(canvas, component_elements[0], properties, attributes)
            else:
                print "WARNING: Cannot find SVG element for component %s" % component_id
                  
        # Set any progress bars (always measure in percentage). Progress bars have
        # their width attribute altered 
        for element in root.xpath('//svg:rect[@class=\'progress\']',namespaces=self.nsmap):
            bounds = g15util.get_bounds(element)
            id = element.get("id")
            if id.endswith("_progress"):
                value = float(properties[id[:-9]])
                if value == 0:
                    value = 0.1
                element.set("width", str((bounds[2] / 100.0) * value))
            else:
                print "WARNING: Found progress element with an ID that doesn't end in _progress"
                
        # Populate any embedded images
         
        for element in root.xpath('//svg:image[@class=\'embedded_image\']',namespaces=self.nsmap):
            id = element.get("title")
            if id != None and id in properties and properties[id] != None:
                file_str = StringIO()
                file_str.write("data:image/png;base64,")
                img_data = StringIO()
                val = properties[id]
                if isinstance(val, cairo.Surface):
                    val.write_to_png(img_data)
                    file_str.write(base64.b64encode(img_data.getvalue()))
                else: 
                    file_str.write(val)
                element.set("{http://www.w3.org/1999/xlink}href", file_str.getvalue())
                
                
        # Shadow is a special text effect useful on the G15. It will take 8 copies of a text element, make
        # them the same color as the background, and render them under the original text element at x-1/y-1,
        # xy-1,x+1/y,x-1/y etc. This makes the text legible if it overlaps other text or an image (
        # at the expense of losing some detail of whatever is underneath)
        idx = 1
        for element in root.xpath('//svg:*[@class=\'shadow\']',namespaces=self.nsmap):
            for x in range(-1, 2):
                for y in range(-1, 2):
                    if x != 0 or y != 0:
                        shadowed = deepcopy(element)
                        shadowed.set("id", shadowed.get("id") + "_" + str(idx))
                        for bound_element in shadowed.iter():
                            bounds = g15util.get_bounds(bound_element)
                            bound_element.set("x", str(bounds[0] + x))
                            bound_element.set("y", str(bounds[1] + y))                        
                        styles = self.parse_css(shadowed.get("style"))
                        if styles == None:
                            styles = {}
                        styles["fill"] = self.screen.applet.driver.get_color_as_hexrgb(g15driver.HINT_BACKGROUND, (255, 255,255))
                        shadowed.set("style", self.format_styles(styles))
                        element.addprevious(shadowed)
                        idx += 1
                        

        # Find all of the  text boxes. This is a hack to get around rsvg not supporting
        # flowText completely. The SVG must contain two elements. The first must have
        # a class attribute of 'textbox' and the ID must be the property key that it 
        # will contain. The next should be the text element (which defines style etc)
        # and must have an id attribute of <propertyKey>_text. The text layer is
        # then rendered by after the SVG using Pango.
        text_boxes = []
        for element in root.xpath('//svg:rect[@class=\'textbox\']',namespaces=self.nsmap):
            id = element.get("id")
            text_node = root.xpath('//*[@id=\'' + id + '_text\']',namespaces=self.nsmap)[0]
            if text_node != None:            
                styles = self.parse_css(text_node.get("style"))                

                # Store the text box
                text_box = TextBox()            
                text_box.text = properties[id]
                text_box.css = styles
                text_boxes.append(text_box)
            
                # Traverse the parents to the root to get any tranlations to apply so the box gets placed at
                # the correct position
                el = element
                list_transforms = [ cairo.Matrix(1.0, 0.0, 0.0, 1.0, float(element.get("x")), float(element.get("y"))) ]
                while el != None:
                    list_transforms += g15util.get_transforms(el)
                    el = el.getparent()
                list_transforms.reverse()
                t =list_transforms[0]
                for i in range(1, len(list_transforms)):
                    t = t.multiply(list_transforms[i])
                args = str(t)[13:-1].split(", ")
                
                text_box.bounds = ( float(args[4]), float(args[5]), float(element.get("width")), float(element.get("height")))
                
                # Remove the textnod SVG element
                text_node.getparent().remove(text_node)
                element.getparent().remove(element)

            
        # Pass the SVG document to the SVG processor if there is one
        if self.svg_processor != None:
            self.svg_processor(self, properties, attributes)
        
        # Pass the SVG document to the theme's python code to manipulate the document if required
        if self.instance != None:
            try :
                getattr(self.instance, "process_svg")
                try :                
                    processing_result = self.instance.process_svg(self.driver, root, properties, self.nsmap)
                except:
                    traceback.print_exc(file=sys.stderr)
            except AttributeError:                
                # Doesn't exist
                pass
        
        # Set the default fill color to be the default foreground. If elements don't specify their
        # own colour, they will inherit this
        
        root_style = root.get("style")
        fg_c = self.screen.driver.get_control_for_hint(g15driver.HINT_FOREGROUND)
        fg_h = None
        if fg_c != None:
            val = fg_c.value
            fg_h = "#%02x%02x%02x" % ( val[0],val[1],val[2] )
            if root_style != None:
                root_styles = self.parse_css(root_style)
            else:
                root_styles = { }
            root_styles["fill"] = fg_h
            root.set("style", self.format_styles(root_styles))
            
        # Encode entities in all the property values
        for key in properties.keys():
            properties[key] = saxutils.escape(str(properties[key]))
                
        xml = etree.tostring(document)
        t = Template(xml)
        xml = t.safe_substitute(properties)
        
        svg = rsvg.Handle()
        try :
            svg.write(xml)
        except:
            traceback.print_exc(file=sys.stderr)
            print xml
        
        svg.close()
        svg.render_cairo(canvas)
         
        if len(text_boxes) > 0:  
            pango_context = pangocairo.CairoContext(canvas)
            pango_context.set_antialias(self.screen.driver.get_antialias()) 
            fo = cairo.FontOptions()
            fo.set_antialias(self.screen.driver.get_antialias())
            if self.screen.driver.get_antialias() == cairo.ANTIALIAS_NONE:
                fo.set_hint_style(cairo.HINT_STYLE_NONE)
                fo.set_hint_metrics(cairo.HINT_METRICS_OFF)
            
            for text_box in text_boxes:
                
                # Parse the CSS                
                css = text_box.css
                font_size = float(css["font-size"][:-2])
                font_family = css["font-family"]
                font_weight = css["font-weight"]
                font_style = css["font-style"]
                text_align = css["text-align"]
#                line_height = "80%"
#                if "line-height" in css:
#                    line_height = css["line-height"]
                if "fill" in css:
                    foreground = css["fill"]
                else:
                    foreground = None
                
                buf = "<span"
                if font_size != None:
                    buf += " size=\"%d\"" % ( int(font_size * 1000) ) 
                if font_style != None:
                    buf += " style=\"%s\"" % font_style
                if font_weight != None:
                    buf += " weight=\"%s\"" % font_weight
                if font_family != None:
                    buf += " font_family=\"%s\"" % font_family                
                if foreground != None and foreground != "none":
                    buf += " foreground=\"%s\"" % foreground
                    
                buf += ">%s</span>" % saxutils.escape(text_box.text)
                
                attr_list = pango.parse_markup(buf)
                
                # Create the layout
                
                layout = pango_context.create_layout()
                
                pangocairo.context_set_font_options(layout.get_context(), fo)      
                layout.set_attributes(attr_list[0])
                layout.set_width(int(pango.SCALE * text_box.bounds[2]))
                layout.set_wrap(pango.WRAP_WORD_CHAR)      
                layout.set_text(text_box.text)
                spacing = 0
                layout.set_spacing(spacing)
                
                # Alignment
                if text_align == "right":
                    layout.set_alignment(pango.ALIGN_RIGHT)
                elif text_align == "center":
                    layout.set_alignment(pango.ALIGN_CENTER)
                else:
                    layout.set_alignment(pango.ALIGN_LEFT)
                
                # Draw text to canvas
                rgb = self.screen.driver.get_color_as_ratios(g15driver.HINT_FOREGROUND, ( 0, 0, 0 ))                
                canvas.set_source_rgb(rgb[0], rgb[1], rgb[2])
                pango_context.save()
                pango_context.rectangle(text_box.bounds[0], text_box.bounds[1], text_box.bounds[2] * 2, text_box.bounds[3])
                pango_context.clip()  
                          
                pango_context.move_to(text_box.bounds[0] , text_box.bounds[1])    
                pango_context.update_layout(layout)
                
                pango_context.show_layout(layout)
                
                pango_context.restore()
                
        # Paint all GTK components that may have been added
        for offscreen_window, offscreen_bounds in self.offscreen_windows:
            pixbuf = offscreen_window.get_as_pixbuf()
            if pixbuf != None:
                image = g15util.pixbuf_to_surface(pixbuf)
                canvas.save()
                canvas.translate(offscreen_bounds[0], offscreen_bounds[1])
                canvas.set_source_surface(image)
                canvas.paint()
                canvas.restore()
        
        # Give the python portion of the theme chance to draw stuff over the SVG
        if self.instance != None:
                        
            try :
                getattr(self.instance, "paint_foreground")
                try :
                    self.instance.paint_foreground(canvas, properties, attributes, processing_result)
                except:
                    traceback.print_exc(file=sys.stderr)
            except AttributeError:                
                # Doesn't exist
                pass
                
        return document