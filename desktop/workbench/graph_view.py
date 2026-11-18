"""Interactive entity network. Uses the same graph contract as JSON, GraphML and API."""
import html
import math
import numpy as np
from PyQt6.QtCore import Qt, QPointF, QTimer
from PyQt6.QtGui import QColor, QBrush, QPen, QPainterPath, QPolygonF, QPainter, QFontMetricsF
from PyQt6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QLineEdit,QPushButton,QCheckBox,
    QSplitter,QTextBrowser,QGraphicsView,QGraphicsScene,QGraphicsRectItem,QGraphicsTextItem,QGraphicsPathItem,QGraphicsPolygonItem,QGraphicsSimpleTextItem)
from .knowledge import build_graph, summary

COLORS={'Quotation':'#d7e9fa','Customer':'#d8f0e7','Material':'#f9e1cf','Specification':'#e8e0f6','Adhesive':'#f4dfec',
        'QuotedItem':'#e0ecf0','CoatingApplication':'#d6eff0','Requirement':'#fff1d7'}

def network_positions(nodes, edges):
    """Deterministic bounded spring layout, independent of categories or hierarchy."""
    n=len(nodes)
    if not n: return {}
    ids={node['id']:i for i,node in enumerate(nodes)}
    angles=np.arange(n)*2*math.pi/n; xy=np.column_stack((np.cos(angles),np.sin(angles)))*max(260,70*math.sqrt(n))
    pairs=[(ids[e['source']],ids[e['target']]) for e in edges if e['source'] in ids and e['target'] in ids]
    for step in range(160):
        delta=xy[:,None,:]-xy[None,:,:]; distance=np.maximum(np.linalg.norm(delta,axis=2),1)
        np.fill_diagonal(distance,np.inf)
        force=(delta*(320.0**2/distance**3)[:,:,None]).sum(axis=1)
        for i,j in pairs:
            diff=xy[j]-xy[i]; length=max(float(np.linalg.norm(diff)),1)
            spring=diff*((length-400)/length)*.08; force[i]+=spring; force[j]-=spring
        force-=xy*.012
        sizes=np.maximum(np.linalg.norm(force,axis=1),1)
        xy+=force*np.minimum((24*(1-step/180))/sizes,1)[:,None]
    # Rectangle separation keeps labels readable after the spring simulation.
    for _ in range(45):
        collisions=0
        for i in range(n):
            for j in range(i):
                diff=xy[i]-xy[j]; overlap=np.array([220.,112.])-abs(diff)
                if (overlap>0).all():
                    axis=int(np.argmin(overlap)); shift=(overlap[axis]+1)/2*(1 if diff[axis]>=0 else -1)
                    xy[i,axis]+=shift; xy[j,axis]-=shift; collisions+=1
        if not collisions: break
    return {node['id']:QPointF(float(xy[i,0]),float(xy[i,1])) for i,node in enumerate(nodes)}

class EntityNode(QGraphicsRectItem):
    def __init__(self,node,pos,callback):
        super().__init__(-96,-36,192,72); self.node=node; self.edges=[]; self.callback=callback
        self.setFlags(self.GraphicsItemFlag.ItemIsMovable|self.GraphicsItemFlag.ItemIsSelectable|self.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setBrush(QBrush(QColor(COLORS.get(node['type'],'#edf0f4')))); self.setPen(QPen(QColor('#6f8b98'),1.5)); self.setZValue(2)
        text=QGraphicsTextItem(self); text.setTextWidth(180); text.setPos(-90,-33)
        metrics=QFontMetricsF(text.font()); lines=[]; line=''
        for char in node['label'].replace('\n',' '):
            if metrics.horizontalAdvance(line+char)>170:
                lines.append(line); line=''
                if len(lines)==2: break
            line+=char
        remainder=node['label'].replace('\n',' ')[sum(map(len,lines)):]
        lines.append(metrics.elidedText(remainder,Qt.TextElideMode.ElideRight,170))
        text.setPlainText('\n'.join(lines)); text.setDefaultTextColor(QColor('#193e51')); text.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setToolTip(node['type']+'\n'+node['label']); self.setPos(pos)
    def itemChange(self,change,value):
        if change==self.GraphicsItemChange.ItemPositionHasChanged:
            for edge in self.edges: edge.update_geometry()
        return super().itemChange(change,value)
    def mousePressEvent(self,event):
        QTimer.singleShot(0,lambda:self.callback(self.node)); super().mousePressEvent(event)

class RelationEdge(QGraphicsPathItem):
    def __init__(self,edge,source,target,callback):
        super().__init__(); self.edge=edge; self.source=source; self.target=target; self.callback=callback
        color=QColor('#25856e' if edge['status']=='verified' else '#b18a43')
        pen=QPen(color,1.7); pen.setStyle(Qt.PenStyle.SolidLine if edge['status']=='verified' else Qt.PenStyle.DashLine); self.setPen(pen)
        self.arrow=QGraphicsPolygonItem(self); self.arrow.setBrush(QBrush(color)); self.arrow.setPen(QPen(color)); self.arrow.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.label=QGraphicsSimpleTextItem(edge['label'],self); self.label.setBrush(QBrush(QColor('#485967'))); self.label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setToolTip(edge['label']+'｜'+('已覆核並核准' if edge['status']=='verified' else '含待覆核來源')+f'｜{len(edge["provenance"])} 筆來源')
        source.edges.append(self); target.edges.append(self); self.update_geometry()
    def update_geometry(self):
        a=self.source.pos(); b=self.target.pos(); dx=b.x()-a.x(); dy=b.y()-a.y(); length=max(math.hypot(dx,dy),1)
        trim=min(96/max(abs(dx),.001),36/max(abs(dy),.001),.45)
        p=QPointF(a.x()+dx*trim,a.y()+dy*trim); q=QPointF(b.x()-dx*trim,b.y()-dy*trim)
        path=QPainterPath(p); path.lineTo(q); self.setPath(path)
        ux,uy=dx/length,dy/length
        self.arrow.setPolygon(QPolygonF([q,QPointF(q.x()-11*ux+5*uy,q.y()-11*uy-5*ux),QPointF(q.x()-11*ux-5*uy,q.y()-11*uy+5*ux)]))
        center=(p+q)/2; self.label.setPos(center.x()-self.label.boundingRect().width()/2,center.y()-20)
    def mousePressEvent(self,event):
        QTimer.singleShot(0,lambda:self.callback(self.edge)); event.accept()

class NetworkCanvas(QGraphicsView):
    def __init__(self):
        super().__init__(); self.setScene(QGraphicsScene(self)); self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(self.DragMode.ScrollHandDrag); self.setTransformationAnchor(self.ViewportAnchor.AnchorUnderMouse)
    def wheelEvent(self,event):
        if event.modifiers()&Qt.KeyboardModifier.ControlModifier:
            scale=1.12 if event.angleDelta().y()>0 else 1/1.12
            if .08<self.transform().m11()*scale<4: self.scale(scale,scale)
            event.accept()
        else: super().wheelEvent(event)
    def fit(self):
        if self.scene().items(): self.fitInView(self.scene().itemsBoundingRect().adjusted(-40,-40,40,40),Qt.AspectRatioMode.KeepAspectRatio)

class KnowledgeGraphView(QWidget):
    def __init__(self,owner):
        super().__init__(); self.owner=owner; self.doc=None; self.graph_data={}; self.references={}; self.node_items={}; self.edge_items=[]
        layout=QVBoxLayout(self); controls=QHBoxLayout(); layout.addLayout(controls)
        self.scope=QComboBox(); self.scope.addItems(['目前文件實體網路','本圖關聯（同企業跨文件）']); controls.addWidget(self.scope)
        self.approved=QCheckBox('只看已核准'); controls.addWidget(self.approved)
        self.query=QLineEdit(); self.query.setPlaceholderText('篩選實體名稱，例如 客戶、鐵材、precote'); controls.addWidget(self.query,1)
        apply=QPushButton('套用'); controls.addWidget(apply); fit=QPushButton('縮放至全圖'); controls.addWidget(fit)
        self.count=QLabel(); self.count.setWordWrap(True); layout.addWidget(self.count)
        split=QSplitter(); layout.addWidget(split,1); self.view=NetworkCanvas(); split.addWidget(self.view)
        self.details=QTextBrowser(); self.details.setOpenLinks(False); self.details.anchorClicked.connect(self.open_source); split.addWidget(self.details); split.setSizes([860,340])
        self.scope.currentIndexChanged.connect(lambda:self.refresh(self.doc)); self.approved.toggled.connect(lambda:self.refresh(self.doc))
        apply.clicked.connect(lambda:self.refresh(self.doc)); self.query.returnPressed.connect(lambda:self.refresh(self.doc)); fit.clicked.connect(self.fit_all)
    def fit_all(self):
        self.focus(); self.view.fit()
    def focus(self,node_ids=None,edge_ids=None):
        for nid,item in self.node_items.items(): item.setOpacity(1 if node_ids is None or nid in node_ids else .22)
        for item in self.edge_items:
            active=edge_ids is None or item.edge['id'] in edge_ids
            item.setOpacity(1 if active else .12); pen=QPen(item.pen()); pen.setWidthF(2.8 if active and edge_ids is not None else 1.7); item.setPen(pen)
    def refresh(self,doc):
        self.doc=doc; self.node_items={}; self.edge_items=[]; self.references={}; self.view.scene().clear(); self.details.clear()
        if not doc: self.count.setText('先選擇文件'); return
        documents=[doc]
        if self.scope.currentIndex():
            from .corpus import source_documents
            documents=[d for d in source_documents(self.owner.store) if d['tenant']==doc['tenant']]
            if not any(d['id']==doc['id'] for d in documents): documents.append(doc)
        graph=build_graph(documents,approved_only=self.approved.isChecked()); self.graph_data=graph
        nodes=[n for n in graph['nodes'] if n['layer']=='entity']; edges=[e for e in graph['edges'] if e['layer']=='semantic']
        if self.scope.currentIndex():
            keep={n['id'] for n in nodes if n.get('document_id')==doc['id']}
            for _ in range(4): keep|={e['target'] if e['source'] in keep else e['source'] for e in edges if e['source'] in keep or e['target'] in keep}
            nodes=[n for n in nodes if n['id'] in keep]
        query=self.query.text().strip().casefold()
        if query:
            keep={n['id'] for n in nodes if query in (n['label']+' '+n['type']).casefold()}
            keep|={e['target'] if e['source'] in keep else e['source'] for e in edges if e['source'] in keep or e['target'] in keep}
            nodes=[n for n in nodes if n['id'] in keep]
        total=len(nodes); nodes=sorted(nodes,key=lambda n:(n.get('document_id')!=doc['id'],n['id']))[:90]; ids={n['id'] for n in nodes}
        edges=[e for e in edges if e['source'] in ids and e['target'] in ids]
        self.count.setText(f'{len(nodes)}／{total} 個實體 · {len(edges)} 條關係 · {len(graph["unresolved"])} 筆缺值／衝突未建實體。'+(' 顯示上限 90；可篩選名稱，完整網路可由語料包輸出。' if total>90 else '')+' 虛線：含待覆核來源；實線：已覆核並核准。')
        positions=network_positions(nodes,edges)
        for n in nodes:
            item=EntityNode(n,positions[n['id']],self.show_node); self.view.scene().addItem(item); self.node_items[n['id']]=item
        for e in edges:
            item=RelationEdge(e,self.node_items[e['source']],self.node_items[e['target']],self.show_edge); self.view.scene().addItem(item); self.edge_items.append(item)
        self.view.scene().setSceneRect(self.view.scene().itemsBoundingRect().adjusted(-150,-150,150,150)); QTimer.singleShot(0,self.view.fit)
        self.details.setHtml('<h3>實體與關係</h3><p>點實體查看屬性；點關係查看來源。拖動節點可整理位置；Ctrl＋滾輪縮放。</p><p>相同企業內的相同名稱可共用參照節點。共用規格不代表同一零件；同名客戶仍須確認身分。</p><p>客戶遮蔽、價格空白及衝突值不會生成假實體。</p>')
    def provenance_html(self,provenances):
        out=[]; seen=set(); self.references={}
        for p in provenances:
            token=(p['document_id'],p['fact_id'])
            if token in seen: continue
            seen.add(token); key=str(len(self.references)); self.references[key]=p
            out.append('<p><a href="source:'+key+'">'+html.escape(p['document_name']+' · v'+str(p['document_version']))+'</a><br>'+html.escape(p['key']+' · '+p['status']+' · '+p['document_status'])+'</p>')
        return ''.join(out) or '<p>尚無可定位來源。</p>'
    def show_node(self,node):
        edges=[e for e in self.graph_data['edges'] if e['layer']=='semantic' and node['id'] in (e['source'],e['target'])]
        self.focus({n for e in edges for n in (e['source'],e['target'])},{e['id'] for e in edges})
        nodes={n['id']:n for n in self.graph_data['nodes']}
        relations=''.join('<li>'+html.escape(nodes[e['source']]['label']+' → '+e['label']+' → '+nodes[e['target']]['label'])+'</li>' for e in edges)
        attrs=''.join('<li>'+html.escape(a.get('label',a['key'])+'：'+a['value']+' '+a.get('unit',''))+'</li>' for a in node.get('attributes',[]))
        provenance=[p for e in edges for p in e['provenance']]+[a['provenance'] for a in node.get('attributes',[]) if 'provenance' in a]
        self.details.setHtml('<h3>'+html.escape(node['label'])+'</h3><p>'+html.escape(node['type'])+'</p><ul>'+attrs+'</ul><h4>相連關係</h4><ul>'+relations+'</ul><h4>原文來源</h4>'+self.provenance_html(provenance))
    def show_edge(self,edge):
        self.focus({edge['source'],edge['target']},{edge['id']})
        nodes={n['id']:n for n in self.graph_data['nodes']}
        self.details.setHtml('<h3>'+html.escape(edge['label'])+'</h3><p>'+html.escape(nodes[edge['source']]['label']+' → '+nodes[edge['target']]['label'])+'</p>'+self.provenance_html(edge['provenance']))
    def open_source(self,url):
        p=self.references.get(url.toString().split(':')[-1])
        if not p: return
        def navigate():
            doc=self.owner.store.get(p['document_id'])
            if doc['version']!=p['document_version']: raise ValueError('來源已更新，請重新整理圖譜')
            fact=next((f for f in summary(doc)['facts'] if f['id']==p['fact_id']),None)
            if fact: self.owner.navigate_fact(doc,fact)
        self.owner.guard(navigate)
