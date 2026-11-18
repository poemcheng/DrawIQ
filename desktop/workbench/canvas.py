from PyQt6.QtCore import Qt, QRectF, QPointF, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QPen, QBrush, QPixmap, QPainter
from PyQt6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsRectItem
from .domain import box_valid
from .paths import resolve_path

class RegionItem(QGraphicsRectItem):
    def __init__(self,region,width,height,canvas,highlight=False):
        b=region['box']; super().__init__(QRectF(b[0]*width,b[1]*height,(b[2]-b[0])*width,(b[3]-b[1])*height))
        self.region=region; self.canvas=canvas; self.sizing=False; self.original=None
        self.layout_only = region['kind'] == 'layout'
        area = (b[2]-b[0]) * (b[3]-b[1])
        # Layout rectangles underlie evidence. Within each layer the smaller
        # measured box remains clickable even when a large box is selected.
        self.setZValue((1 if self.layout_only else 2) + .99 * (1-area))
        self.setFlags(self.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(self.GraphicsItemFlag.ItemIsMovable,self.can_edit())
        self.highlight=highlight
        self.paint_state(False)

    def can_edit(self):
        return not self.layout_only and self.canvas.mode == 'edit'

    def paint_state(self,selected):
        region=self.region; highlight=self.highlight
        palette={'尺寸、公差與符號':'#8664b1','材料與表面處理':'#678b31',
                 '製程與加工要求':'#b77635','檢驗與品質文件':'#2878a1'}
        overlay = region.get('overlay_color')
        color=QColor(overlay or ('#f08d32' if region['kind']=='feature' else palette.get(region.get('engineering_category'),'#178b91')))
        if highlight and not overlay: color=QColor('#ce3c75')
        if selected: color=color.darker(128) if overlay else QColor('#145dce')
        pen=QPen(color,4 if selected else (2 if highlight or self.layout_only else 1))
        pen.setCosmetic(True)
        if self.layout_only:
            pen.setStyle(Qt.PenStyle.DashLine)
        self.setPen(pen)
        fill=QColor(color)
        alpha=24 if highlight else 6
        if self.layout_only:
            alpha=14 if selected else 0
        fill.setAlpha(alpha)
        self.setBrush(QBrush(fill))
        if self.layout_only:
            self.setToolTip(f'{region["layout_label"]}\n方法：{region["source"]}\n版面定位')
            return
        help_text = '點選後可查看、修正或帶入報價欄位' if overlay else '點選檢視來源；移動／縮放模式：拖移或拉右下角'
        self.setToolTip(region.get('engineering_label','文字證據')+' · '+region.get('engineering_category','')+
                        '\n'+region['text'][:400]+'\n方法：'+'；'.join(region.get('methods',[region['source']]))+
                        '\n'+help_text)

    def mousePressEvent(self,event):
        self.original=self.mapRectToScene(self.rect())
        self.canvas.begin_trajectory(event.scenePos(),self.region['id'])
        self.canvas.select_region(self.region['id'])
        corner=self.rect().bottomRight()
        transform=self.canvas.transform()
        tolerance=12/max(.01,(transform.m11()**2+transform.m12()**2)**.5)
        if self.can_edit() and (event.pos()-corner).manhattanLength()<tolerance:
            self.sizing=True; event.accept()
        else: super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        self.canvas.append_trajectory(event.scenePos())
        if self.sizing and self.can_edit():
            point=self.mapFromScene(self.canvas.clamp_point(event.scenePos()))
            rect=QRectF(self.rect().topLeft(),point).normalized()
            if rect.width()>2 and rect.height()>2: self.setRect(rect)
            event.accept()
        else:
            super().mouseMoveEvent(event)
            if self.can_edit():
                rect=self.mapRectToScene(self.rect()); bounds=self.canvas.sceneRect()
                self.moveBy(max(bounds.left()-rect.left(),min(0,bounds.right()-rect.right())),max(bounds.top()-rect.top(),min(0,bounds.bottom()-rect.bottom())))

    def mouseReleaseEvent(self,event):
        if not self.sizing: super().mouseReleaseEvent(event)
        self.sizing=False
        rect=self.mapRectToScene(self.rect())
        canvas=self.canvas; rid=self.region['id']; box=canvas.normalized(rect)
        changed=self.can_edit() and self.original and box!=canvas.normalized(self.original)
        canvas.finish_trajectory('move_resize' if changed else 'select',event.scenePos(),box,rid)
        self.original=None
        # Both signals may refresh UI. Defer until Qt releases the graphics item.
        def notify():
            canvas.region_clicked.emit(rid)
            if changed: canvas.region_changed.emit(rid,box)
        QTimer.singleShot(0,notify)

class DrawingCanvas(QGraphicsView):
    region_created=pyqtSignal(list)
    region_changed=pyqtSignal(str,list)
    region_clicked=pyqtSignal(str)
    mode_changed=pyqtSignal(str)
    gesture_completed=pyqtSignal(dict)
    def __init__(self):
        super().__init__(); self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor('#e9eef0')); self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(self.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(self.DragMode.ScrollHandDrag)
        self.width_px=1; self.height_px=1; self.rotation=0; self.draw_mode=False; self.mode='select'; self.start=None; self.rubber=None; self.region_items={}; self.selected_region=None
        self._trajectory=[]; self._trajectory_start=None

    def begin_trajectory(self,point,region_id=None):
        import time
        self._trajectory_start=time.monotonic(); self._trajectory=[]
        self.append_trajectory(point)

    def append_trajectory(self,point):
        if self._trajectory_start is None: return
        import time
        point=self.clamp_point(point)
        sample=[round(point.x()/self.width_px,6),round(point.y()/self.height_px,6),
                round((time.monotonic()-self._trajectory_start)*1000)]
        if self._trajectory and sample[:2]==self._trajectory[-1][:2]: return
        if len(self._trajectory)>=255: self._trajectory=self._trajectory[::2]
        self._trajectory.append(sample)

    def finish_trajectory(self,action,point,box=None,region_id=None):
        if self._trajectory_start is None: return
        self.append_trajectory(point)
        event={'action':action,'points':self._trajectory,'box':box,'region_id':region_id,
               'display_rotation':self.rotation,'coordinate_space':'original_page_normalized',
               'point_format':['x','y','elapsed_ms'],'mode':self.mode}
        self._trajectory=[]; self._trajectory_start=None
        self.gesture_completed.emit(event)

    def show_page(self,page,regions,highlight_ids=(),show_all=False,keep_view=False):
        transform=self.transform(); center=self.mapToScene(self.viewport().rect().center())
        self.scene().clear(); self.region_items={}; self.rubber=None; self.start=None
        pix=QPixmap(str(resolve_path(page['image_path'])))
        if pix.isNull(): raise ValueError('頁面影像無法讀取')
        self.scene().addPixmap(pix); self.width_px=pix.width(); self.height_px=pix.height()
        self.scene().setSceneRect(0,0,self.width_px,self.height_px)
        for e in regions:
            if e['page']!=page['number'] or (not show_all and e['id'] not in highlight_ids): continue
            item=RegionItem(e,self.width_px,self.height_px,self,e['id'] in highlight_ids)
            self.scene().addItem(item); self.region_items[e['id']]=item
        self.select_region(self.selected_region)
        if keep_view: self.setTransform(transform); self.centerOn(center)
        else: self.rotation=0; self.fit()

    def fit(self):
        self.resetTransform(); self.rotate(self.rotation); self.fitInView(self.sceneRect(),Qt.AspectRatioMode.KeepAspectRatio)

    def focus_box(self,box):
        """Center a measured source with readable context at any display rotation."""
        if not box_valid(box):
            return
        x0,y0,x1,y1=box
        width=min(1,max(.26,x1-x0+.10))
        height=min(1,max(.22,y1-y0+.10))
        x=max(0,min(1-width,(x0+x1-width)/2))
        y=max(0,min(1-height,(y0+y1-height)/2))
        focus=QRectF(x*self.width_px,y*self.height_px,width*self.width_px,height*self.height_px)
        self.fitInView(focus,Qt.AspectRatioMode.KeepAspectRatio)
        self.centerOn(focus.center())

    def rotate_page(self):
        self.rotation=(self.rotation+90)%360; self.fit()

    def set_draw(self,enabled):
        self.set_mode('draw' if enabled else 'select')

    def set_mode(self,mode):
        if mode not in ('select','draw','edit'): raise ValueError('未知操作模式')
        self.cancel_gesture(); self.mode=mode; self.draw_mode=mode=='draw'
        self.setDragMode(self.DragMode.NoDrag if self.draw_mode else self.DragMode.ScrollHandDrag)
        self.setCursor(Qt.CursorShape.CrossCursor if self.draw_mode else Qt.CursorShape.ArrowCursor)
        for item in self.region_items.values():
            item.setFlag(item.GraphicsItemFlag.ItemIsMovable,item.can_edit())
        self.mode_changed.emit(mode)

    def select_region(self,eid):
        previous=self.region_items.get(self.selected_region)
        if previous is not None and eid!=self.selected_region:
            previous.paint_state(False)
        self.selected_region=eid
        item=self.region_items.get(eid)
        if item is not None:
            item.paint_state(True)

    def clamp_point(self,point):
        return QPointF(max(0,min(self.width_px,point.x())),max(0,min(self.height_px,point.y())))

    def cancel_gesture(self):
        if self._trajectory_start is not None:
            point=QPointF(self._trajectory[-1][0]*self.width_px,self._trajectory[-1][1]*self.height_px)
            self.finish_trajectory('cancel',point)
        if self.rubber: self.scene().removeItem(self.rubber)
        self.rubber=None; self.start=None
        for item in self.region_items.values():
            if item.original is not None:
                item.setPos(0,0); item.setRect(item.original); item.original=None; item.sizing=False

    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Escape:
            self.set_mode('select'); event.accept()
        else: super().keyPressEvent(event)

    def normalized(self,rect):
        return [round(rect.left()/self.width_px,6),round(rect.top()/self.height_px,6),round(rect.right()/self.width_px,6),round(rect.bottom()/self.height_px,6)]

    def mousePressEvent(self,event):
        if self.draw_mode and event.button()==Qt.MouseButton.LeftButton:
            self.begin_trajectory(self.mapToScene(event.pos()))
            self.start=self.clamp_point(self.mapToScene(event.pos())); self.rubber=self.scene().addRect(QRectF(self.start,self.start),QPen(QColor('#ce3c75'),2)); event.accept()
        else: super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if self.start is not None and self.rubber:
            self.append_trajectory(self.mapToScene(event.pos()))
            self.rubber.setRect(QRectF(self.start,self.mapToScene(event.pos())).normalized()); event.accept()
        else: super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        if self.start is not None and self.rubber:
            rect=self.rubber.rect().intersected(self.sceneRect()); self.scene().removeItem(self.rubber)
            self.rubber=None; self.start=None
            if rect.width()>5 and rect.height()>5:
                self.finish_trajectory('draw',self.mapToScene(event.pos()),self.normalized(rect))
                box=self.normalized(rect); QTimer.singleShot(0,lambda:self.region_created.emit(box))
            else: self.finish_trajectory('discard_small_box',self.mapToScene(event.pos()))
            event.accept()
        else: super().mouseReleaseEvent(event)

    def wheelEvent(self,event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor=1.15 if event.angleDelta().y()>0 else 1/1.15; self.scale(factor,factor); event.accept()
        else: super().wheelEvent(event)
