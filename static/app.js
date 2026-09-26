const $=s=>document.querySelector(s), $$=s=>document.querySelectorAll(s);
let products=[], categories=[], currentCategory='', user=null, storeSettings=null, currentCartTotal=0, savedAddresses=[];
let cart={}; // V10: cart contents live in SQLite, never in localStorage.
let imageSyncCancelled=false;
const catImages={
'Rice & Grains':'https://hiramart.net/cdn/shop/products/IndiaGate5Kgok_700x700.jpg?v=1642473178',
'Pulses & Dal':'https://www.tatanutrikorner.com/cdn/shop/files/Tata-Sampann-Toor-Dal-1kg-_FOP_-with-Sanjeev-kapoor_1_6c76e8d3-4abb-4ce8-9b91-767a3417bb2a.png?v=1748858295&width=800',
'Flour & Atta':'https://img2.exportersindia.com/product_images/bc-full/2023/5/4241154/aashirvaad-1685433296_6914343_1827284.jpg',
'Cooking Oils':'https://image.cdn.shpy.in/278008/SKU-0935_0-1745058569487.jpg?format=webp',
'Spices & Masala':'https://cdn.dmart.in/images/products/IPowderMasala200gmEVRS3774xx281220_5_B.jpg',
'Sugar, Salt & Sweeteners':'https://i.ebayimg.com/images/g/I~IAAOSwv7JlN~N6/s-l1200.jpg',
'Snacks & Namkeen':'https://www.vishalmegamart.com/dw/image/v2/BGHT_PRD/on/demandware.static/-/Sites-vmm-fmcg-master-catalog/default/dw25ff22f8/images/large/1310003724.jpg?sh=900&sw=900',
'Biscuits & Cookies':'https://www.jiomart.com/images/product/original/491551977/britannia-good-day-butter-cookies-200-g-product-images-o491551977-p491551977-0-202311281825.jpg?im=Resize%3D%281000%2C1000%29',
'Beverages':'https://image.aapkabazar.co/product/557/1776237834298.jpg?type=png',
'Instant & Packaged Foods':'https://cdn.taw9eel.com/media/catalog/product/cache/1/image/519x/9df78eab33525d08d6e5fb8d27136e95/n/s/nstk2083.jpg',
'Personal Care':'https://www.gandhi-bazar.com/cdn/shop/products/colgate-strong-teeth-anticavity-toothpaste-200g.jpg?v=1616096406',
'Home Cleaning':'https://cdn.grofers.com/da/cms-assets/cms/product/25ca9930-0433-4f51-b202-29ecc5052dc7.jpg',
'Baby Care':'https://media-v4.edamama.ph/products/Baby%20Dry%20Pants%20Value%20Medium%20%2834%20pcs%29_1600312353748.jpg',
'Pooja & Devotional':'https://asset.sastasundar.com/incom/images/product/Cycle-Three-In-One-Pure-Agarbathies-SereneYugantar--Jagrane-Free-Match-Box-1622444030-10086600-1.jpg',
'Household Essentials':'https://classicderma.com/cdn/shop/files/DR231127_1.jpg?v=1745740159',
'Stationery':'https://statmo.in/wp-content/uploads/2017/12/Classmate-Spiral-Notebook-200-Pages-600x607.jpg'};

async function api(url,opt={}){
  const headers={...(opt.headers||{})};
  if(!(opt.body instanceof FormData)) headers['Content-Type']='application/json';
  const r=await fetch(url,{...opt,headers});
  let data=null; try{data=await r.json()}catch{}
  if(!r.ok) throw new Error(data?.detail||'Something went wrong');
  return data;
}
function money(n){return '₹'+Number(n).toLocaleString('en-IN',{maximumFractionDigits:2})}
function toast(msg){const t=$('#toast');t.textContent=msg;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2800)}
function esc(s){return String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]))}
function requestPhotos(list=[]){return list.length?`<div class="request-photos">${list.map(a=>`<a href="${esc(a.url)}" target="_blank" title="${esc(a.filename)}"><img src="${esc(a.url)}" alt="Attached photo"></a>`).join('')}</div>`:''}
function paymentBadge(p){if(!p)return '';const cls=String(p.payment_status||'').toLowerCase().replaceAll(' ','-');return `<span class="payment-badge payment-${cls}">${esc(p.payment_status||'')}</span>`}
function paymentStatus(o){return String(o?.payment?.payment_status||'')}

async function init(){
  const [cats,settings]=await Promise.all([api('/api/categories'),api('/api/store-settings')]);
  categories=cats; storeSettings=settings; renderStoreDetails(); renderCategories(); await loadProducts();
  try{user=await api('/api/me')}catch{}
  updateAccountButton(); await renderCart();
}

function renderStoreDetails(){
  if(!storeSettings)return;
  const phone=String(storeSettings.phone||'').replace(/\D/g,'');
  const wa=String(storeSettings.whatsapp||'').replace(/\D/g,'');
  const phoneHref='tel:+91'+phone, waHref='https://wa.me/91'+wa, map=storeSettings.map_url;
  const set=(id,text)=>{const e=$(id);if(e)e.textContent=text};
  const href=(id,url)=>{const e=$(id);if(e)e.href=url};
  set('#contactPhone',storeSettings.phone); set('#contactWhatsApp',storeSettings.whatsapp);
  set('#contactEmail',storeSettings.email); set('#contactAddress',storeSettings.address);
  set('#footerHours','Open: '+storeSettings.hours); set('#topPhone','📞 '+storeSettings.phone);
  href('#topPhone',phoneHref); href('#topWhatsApp',waHref); href('#phoneCard',phoneHref); href('#whatsappCard',waHref);
  href('#emailCard','mailto:'+storeSettings.email); href('#mapCard',map);
  set('#footerPhone','Phone: +91 '+storeSettings.phone); href('#footerPhone',phoneHref);
  set('#footerEmail',storeSettings.email); href('#footerEmail','mailto:'+storeSettings.email);
}

function renderCategories(){
  const nav=$('#navCategories');
  nav.innerHTML=categories.map(c=>`<button class="navcat" data-category="${esc(c)}">${esc(c)}</button>`).join('');
  $('#categoryCards').innerHTML=categories.map(c=>`<div class="category-card" data-category="${esc(c)}"><div class="cat-icon"><img src="${catImages[c]||'/static/images/products/default-product.png'}" alt="${esc(c)}"></div><b>${esc(c)}</b></div>`).join('');
  $$('[data-category]').forEach(x=>x.onclick=()=>selectCategory(x.dataset.category));
}
function selectCategory(c){currentCategory=c;$$('.navcat').forEach(x=>x.classList.toggle('active',x.dataset.category===c));$('#productHeading').textContent=c||'All Products';loadProducts();document.querySelector('#products').scrollIntoView({behavior:'smooth'})}
async function loadProducts(){const q=$('#searchInput').value.trim();const qs=new URLSearchParams();if(q)qs.set('q',q);if(currentCategory)qs.set('category',currentCategory);products=await api('/api/products?'+qs);renderProducts()}
function renderProducts(){const g=$('#productGrid');$('#resultCount').textContent=`${products.length} items`;g.innerHTML=products.map(p=>{const mrp=Number(p.mrp||0),discount=mrp>Number(p.price)?Math.round((mrp-Number(p.price))*100/mrp):0;return `<article class="product-card"><div class="product-image"><img src="${esc(p.image)}" alt="${esc(p.name)}" onerror="this.onerror=null;this.src='/static/images/products/default-product.png'"></div><div class="product-body"><div class="product-cat">${esc(p.category)}</div><h3>${esc(p.name)}</h3><div class="unit">${esc(p.unit)}</div><div class="price-row"><span><span class="price">${money(p.price)}</span>${mrp>Number(p.price)?` <small class="mrp">MRP ${money(mrp)}</small> <small class="discount">${discount}% off</small>`:''}</span><span class="stock ${p.stock<=Number(p.reorder_level||5)?'low':''}">${p.stock>0?p.stock+' in stock':'Out of stock'}</span></div><button class="add" ${p.stock<1?'disabled':''} onclick="addToCart(${p.id})">${p.stock<1?'Out of Stock':'Add to Cart'}</button></div></article>`}).join('')||'<div class="empty">No products found.</div>'}
async function addToCart(id){
  try{const d=await api('/api/cart/items',{method:'POST',body:JSON.stringify({product_id:id,quantity:1})});applyCartPayload(d);toast('Added to cart')}catch(err){toast(err.message)}
}
function applyCartPayload(d){
  cart={}; (d.items||[]).forEach(i=>cart[i.product_id]=i.quantity);
  $('#cartItems').innerHTML=(d.items||[]).map(i=>`<div class="cart-row"><img src="${esc(i.image)}" onerror="this.onerror=null;this.src='/static/images/products/default-product.png'"><div><h4>${esc(i.name)}</h4><small>${esc(i.unit)} · ${money(i.price)}</small><div class="qty"><button onclick="changeQty(${i.product_id},-1)">−</button><b>${i.quantity}</b><button onclick="changeQty(${i.product_id},1)">+</button></div></div><div><b>${money(i.subtotal)}</b><br><button class="remove" onclick="removeCart(${i.product_id})">Remove</button></div></div>`).join('')||'<div class="empty">Your cart is empty.</div>';
  $('#cartCount').textContent=d.count||0; currentCartTotal=Number(d.total||0); $('#cartSubtotal').textContent=money(currentCartTotal); $('#checkoutTotal').textContent=money(currentCartTotal);
  if($('#paymentMethod'))updateUpiBox();
}
async function renderCart(){try{applyCartPayload(await api('/api/cart'))}catch(err){toast(err.message)}}
async function changeQty(id,d){const q=(cart[id]||0)+d;try{const data=await api('/api/cart/items/'+id,{method:'PUT',body:JSON.stringify({quantity:Math.max(0,q)})});applyCartPayload(data)}catch(err){toast(err.message)}}
async function removeCart(id){try{applyCartPayload(await api('/api/cart/items/'+id,{method:'DELETE'}))}catch(err){toast(err.message)}}
function openDrawer(){renderCart();$('#overlay').classList.remove('hidden');$('#cartDrawer').classList.add('open')}
function closeDrawer(){$('#overlay').classList.add('hidden');$('#cartDrawer').classList.remove('open')}
function modal(id,show=true){show?$(id).classList.remove('hidden'):$(id).classList.add('hidden')}
function updateAccountButton(){$('#accountBtn').textContent=user?user.full_name.split(' ')[0]:'Login'}

$('#searchBtn').onclick=loadProducts;
$('#searchInput').addEventListener('keydown',e=>{if(e.key==='Enter')loadProducts()});
$('#cartBtn').onclick=openDrawer; $('#overlay').onclick=closeDrawer;
$$('[data-close]').forEach(b=>b.onclick=closeDrawer);
$$('[data-modal-close]').forEach(b=>b.onclick=()=>b.closest('.modal').classList.add('hidden'));
$$('.tab').forEach(t=>t.onclick=()=>{$$('.tab').forEach(x=>x.classList.toggle('active',x===t));$('#loginForm').classList.toggle('hidden',t.dataset.tab!=='login');$('#registerForm').classList.toggle('hidden',t.dataset.tab!=='register')});
$('#accountBtn').onclick=async()=>{if(!user){modal('#authModal');return}await showAccount()};

$('#loginForm').onsubmit=async e=>{e.preventDefault();const f=new FormData(e.target);try{const d=await api('/api/login',{method:'POST',body:JSON.stringify(Object.fromEntries(f))});user=d.user;modal('#authModal',false);updateAccountButton();await renderCart();toast('Welcome back')}catch(err){toast(err.message)}};
$('#registerForm').onsubmit=async e=>{e.preventDefault();const f=new FormData(e.target);try{await api('/api/register',{method:'POST',body:JSON.stringify(Object.fromEntries(f))});user=await api('/api/me');modal('#authModal',false);updateAccountButton();await renderCart();toast('Account created — add phone and address in Profile')}catch(err){toast(err.message)}};

async function loadCheckoutAddresses(){
  if(!user||user.role!=='customer')return;
  try{savedAddresses=await api('/api/addresses')}catch{savedAddresses=[]}
  const sel=$('#savedAddressSelect'); if(!sel)return;
  sel.innerHTML='<option value="">Use profile / enter address manually</option>'+savedAddresses.map(a=>`<option value="${a.id}" ${a.is_default?'selected':''}>${esc(a.label)} — ${esc(a.address).slice(0,70)}</option>`).join('');
  sel.onchange=()=>{const a=savedAddresses.find(x=>String(x.id)===sel.value);if(a){const f=$('#checkoutForm');f.elements.customer_name.value=a.recipient_name||user.full_name||'';f.elements.phone.value=a.phone||user.phone||'';f.elements.address.value=a.address||''}};
}
async function prefillCheckout(){
  if(!user)return;
  const form=$('#checkoutForm');
  form.elements.customer_name.value=user.full_name||'';
  form.elements.phone.value=user.phone||'';
  form.elements.address.value=user.address||'';
  await loadCheckoutAddresses();
  const def=savedAddresses.find(a=>a.is_default); if(def){form.elements.customer_name.value=def.recipient_name;form.elements.phone.value=def.phone;form.elements.address.value=def.address}
}
$('#checkoutBtn').onclick=async()=>{
  if(!Object.keys(cart).length){toast('Cart is empty');return}
  if(!user){closeDrawer();modal('#authModal');toast('Please login before checkout');return}
  closeDrawer();await renderCart();await prefillCheckout();modal('#checkoutModal');updateUpiBox();
};
function updateUpiBox(){const method=$('#paymentMethod')?.value;const box=$('#upiBox');if(!box)return;const payNow=method==='UPI Payment';const upiLater=method==='UPI on Delivery';box.classList.toggle('hidden',!payNow);const ref=$('#upiReference');if(ref)ref.required=payNow;const later=$('#upiDeliveryNote');if(later)later.classList.toggle('hidden',!upiLater);if(!payNow)return;$('#upiAmount').textContent=money(currentCartTotal);const configured=!!String(storeSettings?.upi_id||'').trim();$('#upiConfigured').classList.toggle('hidden',!configured);$('#upiNotConfigured').classList.toggle('hidden',configured);if(configured){$('#upiIdText').textContent=storeSettings.upi_id;$('#upiQr').src='/api/upi-qr?amount='+encodeURIComponent(currentCartTotal)+'&t='+Date.now()}}
$('#paymentMethod').addEventListener('change',updateUpiBox);
$('#checkoutForm').onsubmit=async e=>{
  e.preventDefault(); const f=Object.fromEntries(new FormData(e.target));
  if(f.payment_method!=='UPI Payment')f.upi_reference='';
  if((f.payment_method==='UPI Payment'||f.payment_method==='UPI on Delivery')&&!String(storeSettings?.upi_id||'').trim()){toast('UPI ID is not configured yet');return}
  try{const d=await api('/api/orders',{method:'POST',body:JSON.stringify(f)});await renderCart();modal('#checkoutModal',false);e.target.reset();updateUpiBox();toast(`Order #${d.order_id} placed. Confirmation email queued.`);await loadProducts()}catch(err){toast(err.message)}
};

function openDeliveryUpi(orderId,amount){
  if(!String(storeSettings?.upi_id||'').trim()){toast('Store UPI ID is not configured yet');return}
  document.querySelector('#deliveryUpiModal')?.remove();
  const upiId=String(storeSettings.upi_id||'').trim();
  const payee=String(storeSettings.upi_payee_name||'Maneendra General Stores').trim();
  const upiUri=`upi://pay?pa=${encodeURIComponent(upiId)}&pn=${encodeURIComponent(payee)}&am=${encodeURIComponent(Number(amount).toFixed(2))}&cu=INR&tn=${encodeURIComponent('Maneendra General Stores Order #'+orderId)}`;
  document.body.insertAdjacentHTML('beforeend',`<div class="modal" id="deliveryUpiModal"><div class="modal-card"><button class="modal-x" onclick="document.querySelector('#deliveryUpiModal')?.remove()">×</button><span class="eyebrow">PAY AT DELIVERY</span><h2>Pay ${money(amount)} by UPI</h2><p class="muted">Scan the QR or open your UPI app. After payment, enter the transaction/reference ID below.</p><div class="delivery-upi-pay"><img src="/api/upi-qr?amount=${encodeURIComponent(amount)}&t=${Date.now()}" alt="UPI QR"><div><b>${esc(payee)}</b><p>${esc(upiId)}</p><a class="primary upi-app-link" href="${upiUri}">Open UPI App</a></div></div><label class="delivery-upi-ref">UPI transaction / reference ID<input id="deliveryUpiRef" maxlength="120" placeholder="Enter reference after payment"></label><div class="hint">Payment will show as Pending Verification until the store checks it. Do not share the delivery OTP until payment is confirmed.</div><button class="primary wide" onclick="submitDeliveryUpi(${orderId})">I have paid — submit for verification</button></div></div>`);
}
async function submitDeliveryUpi(orderId){
  const ref=$('#deliveryUpiRef')?.value?.trim();
  if(!ref||ref.length<4){toast('Enter the UPI transaction/reference ID');return}
  try{const d=await api(`/api/orders/${orderId}/switch-to-upi`,{method:'POST',body:JSON.stringify({upi_reference:ref})});document.querySelector('#deliveryUpiModal')?.remove();toast('UPI submitted — waiting for store verification');await showCustomerAccount($('#accountContent'))}catch(err){toast(err.message)}
}

async function logout(){await api('/api/logout',{method:'POST'});user=null;modal('#accountModal',false);updateAccountButton();await renderCart();toast('Logged out')}

async function showAccount(){
  const box=$('#accountContent');
  if(user.role==='admin') await showAdmin(box); else await showCustomerAccount(box);
  modal('#accountModal');
}

async function showCustomerAccount(box){
  const [profile,orders,addresses]=await Promise.all([api('/api/profile'),api('/api/orders/me'),api('/api/addresses')]);
  savedAddresses=addresses;
  user=profile; updateAccountButton();
  const requestHistory=[];
  const orderHtml=orders.length?orders.map(o=>{
    const items=o.items.filter(i=>i.quantity>0);
    if(o.cancellation)requestHistory.push({type:'Cancellation',order:o.id,created_at:o.cancellation.created_at,status:o.cancellation.status,reason:o.cancellation.reason,attachments:o.cancellation.attachments||[]});
    (o.returns||[]).forEach(r=>requestHistory.push({type:'Return',order:o.id,created_at:r.created_at,status:r.status,reason:r.reason,attachments:r.attachments||[],product:(o.items.find(i=>i.id===r.order_item_id)||{}).product_name,quantity:r.quantity,refund:r.refund_amount}));
    const returnRows=(o.returns||[]).map(r=>`<div class="return-row"><span>Return #${r.id} · Qty ${r.quantity}</span><b class="return-${String(r.status).toLowerCase()}">${esc(r.status)}</b>${r.refund_amount?`<span>Refund: ${money(r.refund_amount)} · ${esc(r.refund_status||'Pending')}</span>`:''}<small>${esc(r.reason)}</small>${requestPhotos(r.attachments||[])}</div>`).join('');
    const itemRows=items.map(i=>{
      const used=(o.returns||[]).filter(r=>r.order_item_id===i.id&&['Pending','Approved'].includes(r.status)).reduce((a,r)=>a+r.quantity,0);
      const left=Math.max(0,i.quantity-used);
      return `<div class="customer-order-item"><div><b>${esc(i.product_name)}</b><small>${esc(i.unit)} · ${money(i.price)} × ${i.quantity}</small></div><span>${money(i.price*i.quantity)}</span>${o.can_return&&left>0?`<button class="mini" onclick="openReturnRequest(${o.id},${i.id},${left},'${String(i.product_name).replaceAll("'","\\'")}')">Return</button>`:''}</div>`;
    }).join('');
    const adjusted=Number(o.adjustment_amount)>0;
    const financial=`<div class="order-financials"><span>Original total <b>${money(o.original_total)}</b></span>${adjusted?`<span>Revised total <b>${money(o.revised_total)}</b></span><span class="saving">Unavailable-item difference <b>${money(o.adjustment_amount)}</b></span>`:''}${o.payment_method==='Cash on Delivery'?`<span>Cash payable <b>${money(o.amount_payable)}</b></span>`:(o.payment_method==='UPI on Delivery'&&paymentStatus(o)!=='Payment Confirmed'?`<span>UPI due on delivery <b>${money(o.revised_total)}</b></span>`:o.refund_due>0?`<span class="refund">Refund due <b>${money(o.refund_due)}</b></span>`:'')}</div>`;
    const history=(o.status_history||[]).map(h=>`<span class="history-step"><b>${esc(h.status)}</b><small>${new Date(h.created_at).toLocaleString()}</small></span>`).join('');
    const payment=o.payment||{};
    const paymentLine=`<p class="order-payment">Payment: <b>${esc(o.payment_method)}</b>${o.upi_reference?` · Ref ${esc(o.upi_reference)}`:''} ${paymentBadge(payment)}</p>${['UPI Payment','UPI on Delivery'].includes(o.payment_method)&&payment.payment_status==='Pending Verification'?'<div class="payment-note pending">Your UPI payment is waiting for store verification.</div>':''}${o.payment_method==='UPI on Delivery'&&payment.payment_status==='UPI Due on Delivery'?'<div class="payment-note pending">Pay by UPI when the order reaches you. The QR will appear once the order is Out for Delivery.</div>':''}${payment.payment_status==='Payment Confirmed'?'<div class="payment-note confirmed">✓ Your UPI payment has been confirmed by the store.</div>':''}`;
    const cancelInfo=o.cancellation?`<div class="request-summary cancel-summary"><b>Cancellation reason</b><p>${esc(o.cancellation.reason)}</p>${requestPhotos(o.cancellation.attachments||[])}</div>`:'';
    const deliveryNote=o.status==='Out for Delivery'?(o.payment_method==='Cash on Delivery'?`<div class="otp-customer-note">🔐 Delivery OTP sent to your registered email. Cash due: <b>${money(o.amount_payable)}</b>. You may still switch to UPI below. Share the OTP only after receiving the order and completing payment.</div><button class="mini strong order-action upi-switch-btn" onclick="openDeliveryUpi(${o.id},${Number(o.revised_total)||0})">Pay by UPI instead</button>`:o.payment_method==='UPI on Delivery'?(payment.payment_status==='Payment Confirmed'?`<div class="otp-customer-note">🔐 UPI payment is confirmed. A delivery OTP was sent to your email. Share it only after receiving the order.</div>`:payment.payment_status==='Pending Verification'?`<div class="otp-customer-note">🔐 UPI payment submitted and awaiting store verification. Do not share the delivery OTP yet.</div>`:`<div class="otp-customer-note">🔐 Your order is here. Pay <b>${money(o.revised_total)}</b> by UPI, submit the UTR/reference, wait for store confirmation, then share the delivery OTP.</div><button class="mini strong order-action upi-switch-btn" onclick="openDeliveryUpi(${o.id},${Number(o.revised_total)||0})">Pay by UPI on Delivery</button>`):`<div class="otp-customer-note">🔐 A delivery OTP was sent to your registered email. ${payment.payment_status==='Payment Confirmed'?'Your UPI payment is confirmed. Share the OTP only after receiving your order.':'Your UPI payment is awaiting store verification. Do not share the OTP until payment is confirmed.'}</div>`):'';
    return `<div class="order-card customer-order-card"><div class="order-top"><div><b>Order #${o.id}</b><p>${new Date(o.created_at).toLocaleString()}</p></div><span class="badge status-${String(o.status).toLowerCase().replaceAll(' ','-')}">${esc(o.status)}</span></div><div class="customer-order-items">${itemRows||'<small>No items remaining in this order.</small>'}</div>${financial}${history?`<div class="order-history-line">${history}</div>`:''}<p class="order-address">Delivery: ${esc(o.address)}</p>${paymentLine}<div class="order-quick-actions"><button class="mini" onclick="buyAgain(${o.id})">↻ Buy Again</button><a class="mini link-mini" href="/api/orders/${o.id}/invoice" target="_blank">Preview Bill</a><a class="mini link-mini" href="/api/orders/${o.id}/invoice?receipt=true" target="_blank">Receipt</a>${o.invoice_available?`<a class="mini link-mini invoice-download" href="/api/orders/${o.id}/invoice.pdf" target="_blank">⬇ Final PDF Bill</a>`:''}</div>${o.invoice?`<div class="invoice-status-line"><span>🧾 ${esc(o.invoice.invoice_number)}</span><span class="${o.invoice.email_status==='Sent'?'invoice-sent':'invoice-pending'}">${o.invoice.email_status==='Sent'?'✓ Bill emailed':'Email: '+esc(o.invoice.email_status||'Not sent')}</span></div>`:''}${o.can_cancel?`<button class="mini danger order-action" onclick="openCancelRequest(${o.id})">Cancel Order</button>`:''}${deliveryNote}${o.can_return?'<div class="return-note">Delivered items can be requested for return within 7 days. Add a reason and up to 3 photos if useful.</div>':''}${cancelInfo}${returnRows?`<div class="return-list"><b>Return requests</b>${returnRows}</div>`:''}</div>`;
  }).join(''):'<div class="empty">No orders yet.</div>';
  requestHistory.sort((a,b)=>String(b.created_at).localeCompare(String(a.created_at)));
  const requestsHtml=requestHistory.length?requestHistory.map(r=>`<div class="request-history-card"><div><b>${esc(r.type)} · Order #${r.order}</b><small>${new Date(r.created_at).toLocaleString()}</small></div><span class="badge">${esc(r.status)}</span>${r.product?`<p><b>Product:</b> ${esc(r.product)} · Qty ${r.quantity}</p>`:''}<p><b>Reason:</b> ${esc(r.reason)}</p>${r.refund?`<p><b>Refund:</b> ${money(r.refund)}</p>`:''}${requestPhotos(r.attachments)}</div>`).join(''):'<div class="empty">No cancellation or return requests yet.</div>';

  const addressesHtml=savedAddresses.length?savedAddresses.map(a=>`<div class="address-card ${a.is_default?'default':''}"><div><b>${esc(a.label)} ${a.is_default?'<span class="default-pill">Default</span>':''}</b><p>${esc(a.recipient_name)} · ${esc(a.phone)}</p><small>${esc(a.address)}</small></div><div><button class="mini" onclick="editSavedAddress(${a.id})">Edit</button> ${!a.is_default?`<button class="mini" onclick="makeDefaultAddress(${a.id})">Make default</button>`:''} <button class="mini danger" onclick="deleteSavedAddress(${a.id})">Delete</button></div></div>`).join(''):'<div class="empty">No saved addresses yet.</div>';

  box.innerHTML=`
    <div class="account-head"><div><span class="eyebrow">MY ACCOUNT</span><h2>${esc(user.full_name)}</h2><p class="muted">${esc(user.email)}</p></div><button class="ghost" onclick="logout()">Logout</button></div>
    <div class="customer-tabs"><button class="active" data-customer-tab="profile" onclick="customerSection('profile')">Profile</button><button data-customer-tab="addresses" onclick="customerSection('addresses')">Saved Addresses</button><button data-customer-tab="orders" onclick="customerSection('orders')">My Orders</button><button data-customer-tab="requests" onclick="customerSection('requests')">Returns & Cancellations</button></div>
    <section id="customerProfile">
      <div class="profile-grid">
        <div class="profile-panel"><h3>Personal Details</h3><p class="muted">These details are saved permanently and can be changed anytime.</p>
          <form id="profileForm" class="form">
            <label>Full name<input name="full_name" value="${esc(user.full_name)}" required></label>
            <label>Email<input type="email" name="email" value="${esc(user.email)}" required></label>
            <label>Mobile number<input name="phone" value="${esc(user.phone||'')}" placeholder="Your mobile number"></label>
            <label>Default delivery address<textarea name="address" rows="4" placeholder="House / street / village / city / PIN">${esc(user.address||'')}</textarea></label>
            <button class="primary">Save Profile</button>
          </form>
        </div>
        <div class="profile-panel"><h3>Change Password</h3><p class="muted">You can change your login password whenever needed.</p>
          <form id="passwordForm" class="form">
            <label>Current password<input type="password" name="current_password" required></label>
            <label>New password<input type="password" name="new_password" minlength="6" required></label>
            <button class="ghost strong">Change Password</button>
          </form>
          <div class="mail-note"><b>📧 Order email updates</b><p>Order confirmations, payment confirmations, order changes, delivery OTPs and status updates are sent to <strong>${esc(user.email)}</strong> when store email is configured.</p></div>
        </div>
      </div>
    </section>
    <section id="customerAddresses" class="hidden"><div class="section-mini-head"><div><h3>Saved Addresses</h3><p class="muted">Save Home, Work or other delivery addresses and choose them at checkout.</p></div></div><div class="address-grid">${addressesHtml}</div><div class="profile-panel address-editor"><h3 id="addressFormTitle">Add Address</h3><form id="addressForm" class="form"><input type="hidden" name="address_id" value=""><div class="two"><label>Label<input name="label" value="Home" placeholder="Home / Work" required></label><label>Recipient name<input name="recipient_name" value="${esc(user.full_name)}" required></label></div><label>Phone<input name="phone" value="${esc(user.phone||'')}" required></label><label>Address<textarea name="address" rows="3" required></textarea></label><label class="checkline"><input type="checkbox" name="is_default"> Make this the default address</label><button class="primary">Save Address</button></form></div></section>
    <section id="customerOrders" class="hidden"><div class="section-mini-head"><div><h3>My Orders</h3><p class="muted">Cancel eligible orders, request returns after delivery and see payment/revised totals.</p></div></div>${orderHtml}</section>
    <section id="customerRequests" class="hidden"><div class="section-mini-head"><div><h3>Returns & Cancellations</h3><p class="muted">Your reasons and attached photos are saved permanently with the request.</p></div></div>${requestsHtml}</section>`;
  $('#profileForm').onsubmit=saveProfile;
  $('#passwordForm').onsubmit=changePassword;
  $('#addressForm').onsubmit=saveAddress;
}

function customerSection(which){
  $('#customerProfile').classList.toggle('hidden',which!=='profile');
  $('#customerAddresses').classList.toggle('hidden',which!=='addresses');
  $('#customerOrders').classList.toggle('hidden',which!=='orders');
  $('#customerRequests').classList.toggle('hidden',which!=='requests');
  $$('[data-customer-tab]').forEach(b=>b.classList.toggle('active',b.dataset.customerTab===which));
}
async function saveProfile(e){
  e.preventDefault(); const data=Object.fromEntries(new FormData(e.target));
  try{const d=await api('/api/profile',{method:'PUT',body:JSON.stringify(data)});user=d.user;updateAccountButton();toast('Profile saved permanently');await showCustomerAccount($('#accountContent'))}catch(err){toast(err.message)}
}
async function changePassword(e){
  e.preventDefault(); const data=Object.fromEntries(new FormData(e.target));
  try{await api('/api/profile/password',{method:'PUT',body:JSON.stringify(data)});e.target.reset();toast('Password changed successfully')}catch(err){toast(err.message)}
}
async function saveAddress(e){
  e.preventDefault();const fd=new FormData(e.target),id=fd.get('address_id');const data={label:fd.get('label'),recipient_name:fd.get('recipient_name'),phone:fd.get('phone'),address:fd.get('address'),is_default:fd.get('is_default')==='on'};
  try{await api(id?`/api/addresses/${id}`:'/api/addresses',{method:id?'PUT':'POST',body:JSON.stringify(data)});toast(id?'Address updated':'Address saved');await showCustomerAccount($('#accountContent'));customerSection('addresses')}catch(err){toast(err.message)}
}
function editSavedAddress(id){const a=savedAddresses.find(x=>x.id===id);if(!a)return;customerSection('addresses');const f=$('#addressForm');f.elements.address_id.value=a.id;f.elements.label.value=a.label;f.elements.recipient_name.value=a.recipient_name;f.elements.phone.value=a.phone;f.elements.address.value=a.address;f.elements.is_default.checked=!!a.is_default;$('#addressFormTitle').textContent='Edit Address';f.scrollIntoView({behavior:'smooth',block:'center'})}
async function makeDefaultAddress(id){const a=savedAddresses.find(x=>x.id===id);if(!a)return;try{await api(`/api/addresses/${id}`,{method:'PUT',body:JSON.stringify({label:a.label,recipient_name:a.recipient_name,phone:a.phone,address:a.address,is_default:true})});toast('Default address updated');await showCustomerAccount($('#accountContent'));customerSection('addresses')}catch(err){toast(err.message)}}
async function deleteSavedAddress(id){if(!confirm('Delete this saved address?'))return;try{await api(`/api/addresses/${id}`,{method:'DELETE'});toast('Address deleted');await showCustomerAccount($('#accountContent'));customerSection('addresses')}catch(err){toast(err.message)}}
async function buyAgain(id){try{const d=await api(`/api/orders/${id}/buy-again`,{method:'POST'});applyCartPayload(d.cart);toast(d.unavailable?.length?`Added available items. ${d.unavailable.length} unavailable.`:'Order items added to cart');openDrawer()}catch(err){toast(err.message)}}
let requestReturnFocus=null;
let requestParentScroll={section:null,top:0};
function openRequestModal(title,html,onSubmit){
  // Return/cancel is a nested dialog. Keep My Account open underneath it.
  requestReturnFocus=document.activeElement;
  const activeSection=$('#accountContent > section:not(.hidden)');
  requestParentScroll={section:activeSection,top:activeSection?activeSection.scrollTop:0};
  $('#requestTitle').textContent=title;
  $('#requestFields').innerHTML=html;
  $('#requestForm').onsubmit=onSubmit;
  modal('#requestModal');
  requestAnimationFrame(()=>{
    const first=$('#requestModal textarea, #requestModal input:not([type="hidden"]), #requestModal select');
    if(first) first.focus({preventScroll:true});
  });
}
function closeRequestModal(){
  modal('#requestModal',false);
  $('#requestForm').reset();
  $('#requestFields').innerHTML='';
  requestAnimationFrame(()=>{
    if(requestParentScroll.section && document.body.contains(requestParentScroll.section)){
      requestParentScroll.section.scrollTop=requestParentScroll.top||0;
    }
    if(requestReturnFocus && document.body.contains(requestReturnFocus)){
      try{requestReturnFocus.focus({preventScroll:true})}catch{}
    }
    requestReturnFocus=null;
  });
}
function openCancelRequest(id){
  openRequestModal(`Cancel Order #${id}`,`<p class="muted">Please tell us why you are cancelling. You may attach up to 3 JPG/PNG/WEBP photos.</p><label>Reason for cancellation<textarea name="reason" rows="4" minlength="3" required placeholder="Reason for cancelling this order"></textarea></label><label>Photos (optional)<input type="file" name="files" accept="image/jpeg,image/png,image/webp" multiple></label><button class="danger wide" type="submit">Confirm Cancellation</button>`,async e=>{
    e.preventDefault();if(!confirm(`Cancel order #${id}?`))return;
    const fd=new FormData(e.target);
    try{const r=await fetch(`/api/orders/${id}/cancel`,{method:'POST',body:fd});let d={};try{d=await r.json()}catch{}if(!r.ok)throw new Error(d.detail||'Cancellation failed');closeRequestModal();toast('Order cancelled. Reason and photos saved.');await showCustomerAccount($('#accountContent'));customerSection('requests');await loadProducts()}catch(err){toast(err.message)}
  });
}
function openReturnRequest(orderId,itemId,maxQty,productName){
  openRequestModal(`Return ${productName}`,`<p class="muted">Return within 7 days of delivery. Add a clear reason and up to 3 photos if the item is damaged/wrong.</p><input type="hidden" name="order_item_id" value="${itemId}"><label>Return quantity<input type="number" name="quantity" min="1" max="${maxQty}" value="1" required></label><label>Reason for return<textarea name="reason" rows="4" minlength="3" required placeholder="Damaged / wrong item / other reason"></textarea></label><label>Photos (optional)<input type="file" name="files" accept="image/jpeg,image/png,image/webp" multiple></label><button class="primary wide" type="submit">Submit Return Request</button>`,async e=>{
    e.preventDefault();const fd=new FormData(e.target);
    try{const r=await fetch(`/api/orders/${orderId}/return`,{method:'POST',body:fd});let d={};try{d=await r.json()}catch{}if(!r.ok)throw new Error(d.detail||'Return request failed');closeRequestModal();toast('Return request submitted with reason/photos');await showCustomerAccount($('#accountContent'));customerSection('requests')}catch(err){toast(err.message)}
  });
}


function adminOrderCard(o){
  const editable=!['Out for Delivery','Delivered','Cancelled'].includes(o.status);
  const statusChoices=['Placed','Confirmed','Packed','Out for Delivery','Cancelled'];
  const statusControl=(o.status==='Delivered'||o.status==='Cancelled')?`<span class="badge status-${String(o.status).toLowerCase().replaceAll(' ','-')}">${esc(o.status)}</span>`:`<select class="order-status-select" onchange="setOrderStatus(${o.id},this.value)">${statusChoices.map(s=>`<option ${s===o.status?'selected':''}>${s}</option>`).join('')}</select>`;
  const items=o.items.map(i=>`<div class="admin-order-item"><div><b>${esc(i.product_name)}</b><small>${esc(i.unit)} · ${money(i.price)} each · originally ${i.original_quantity||i.quantity}</small></div>${editable?`<label>Qty<input data-order="${o.id}" data-order-item="${i.id}" type="number" min="0" max="${i.quantity}" value="${i.quantity}"></label>`:`<b>× ${i.quantity}</b>`}<span>${money(i.price*i.quantity)}</span></div>`).join('');
  const returns=(o.returns||[]).map(r=>`<div class="admin-return"><div><b>${esc(r.product_name)}</b> × ${r.quantity}<small><b>Reason:</b> ${esc(r.reason)}</small>${requestPhotos(r.attachments||[])}</div><span class="return-${String(r.status).toLowerCase()}">${esc(r.status)}</span>${r.status==='Pending'?`<div><button class="mini" onclick="resolveReturn(${r.id},'Approved')">Approve</button> <button class="mini danger" onclick="resolveReturn(${r.id},'Rejected')">Reject</button></div>`:r.refund_amount?`<div><b>${money(r.refund_amount)} refund · ${esc(r.refund_status||'Pending')}</b>${(r.refund_status||'Pending')==='Pending'?` <button class="mini strong" onclick="markReturnRefunded(${r.id})">Mark Refunded</button>`:''}</div>`:''}</div>`).join('');
  const cancel=o.cancellation?`<div class="admin-cancel"><h4>Customer cancellation</h4><p><b>Reason:</b> ${esc(o.cancellation.reason)}</p>${requestPhotos(o.cancellation.attachments||[])}</div>`:'';
  const p=o.payment||{};
  const isUpi=['UPI Payment','UPI on Delivery'].includes(o.payment_method);
  const canConfirmUpi=isUpi&&p.payment_status==='Pending Verification'&&!!o.upi_reference;
  const paymentBox=isUpi?`<div class="admin-payment-box"><div><b>${esc(o.payment_method)}</b><small>${o.upi_reference?`Ref: ${esc(o.upi_reference)} · `:''}Amount ${money(p.current_amount??o.revised_total)}</small></div><div>${paymentBadge(p)} ${canConfirmUpi?`<button class="mini strong" onclick="markPaymentReceived(${o.id})">Mark Payment Received</button>`:(p.payment_status==='Payment Confirmed'?`<small>${p.confirmed_at?'Confirmed '+new Date(p.confirmed_at).toLocaleString():''}</small>`:'')}</div></div>`:`<div class="admin-payment-box"><div><b>Cash on Delivery</b><small>${o.status==='Out for Delivery'?`Collect ${money(o.amount_payable)} before OTP verification`:`Amount ${money(o.amount_payable)}`}</small></div>${paymentBadge(p)}</div>`;
  const upiAwaiting=isUpi&&p.payment_status!=='Payment Confirmed';
  const otpBox=o.status==='Out for Delivery'?`<div class="otp-admin-box"><div><b>Verify delivery OTP</b><small>${o.payment_method==='Cash on Delivery'?`Customer OTP email says cash due: ${money(o.amount_payable)}. Collect cash first, then enter OTP.`:(upiAwaiting?(o.payment_method==='UPI on Delivery'&&p.payment_status==='UPI Due on Delivery'?'Customer selected UPI on Delivery and has not submitted payment yet.':'Confirm the UPI payment first; OTP delivery completion is locked until then.'):'UPI payment is confirmed. Enter the customer OTP after handing over the order.')}</small></div><div><input id="deliveryOtp${o.id}" inputmode="numeric" maxlength="6" placeholder="6-digit OTP" ${upiAwaiting?'disabled':''}><button class="primary" onclick="verifyDelivery(${o.id})" ${upiAwaiting?'disabled':''}>${upiAwaiting?'Confirm UPI first':'Verify & Deliver'}</button></div></div>`:'';
  return `<div class="admin-order-card" id="admin-order-${o.id}"><div class="admin-order-head"><div><span class="eyebrow">ORDER #${o.id}</span><h3>${esc(o.customer_name)}</h3><p>${esc(o.phone)} · ${esc(o.email)}<br>${new Date(o.created_at).toLocaleString()}</p></div><div class="admin-order-status">${statusControl}</div></div><div class="admin-order-items">${items}</div>${editable?`<button class="mini strong-order-btn" onclick="saveOrderItems(${o.id})">Save quantity changes</button><p class="hint">Use 0 for an unavailable item. Quantities can only be reduced. Returned stock is added back automatically.</p>`:''}<div class="order-financials admin-financials"><span>Original <b>${money(o.original_total)}</b></span><span>Current <b>${money(o.revised_total)}</b></span>${o.adjustment_amount>0?`<span class="saving">Difference <b>${money(o.adjustment_amount)}</b></span>`:''}${o.payment_method==='Cash on Delivery'?`<span>Customer pays <b>${money(o.amount_payable)}</b></span>`:o.refund_due>0?`<span class="refund">Refund due <b>${money(o.refund_due)}</b></span>`:''}</div>${paymentBox}${otpBox}<div class="order-quick-actions"><a class="mini link-mini" href="/api/orders/${o.id}/invoice" target="_blank">Preview Bill</a><a class="mini link-mini" href="/api/orders/${o.id}/invoice?receipt=true" target="_blank">Receipt</a>${o.invoice_available?`<a class="mini link-mini invoice-download" href="/api/orders/${o.id}/invoice.pdf" target="_blank">Final PDF</a><button class="mini invoice-resend" onclick="resendInvoice(${o.id})">✉ Resend bill</button>`:''}</div>${o.invoice?`<div class="invoice-status-line admin-invoice-line"><span>🧾 ${esc(o.invoice.invoice_number)}</span><span class="${o.invoice.email_status==='Sent'?'invoice-sent':'invoice-pending'}">${o.invoice.email_status==='Sent'?'✓ emailed '+(o.invoice.emailed_at?new Date(o.invoice.emailed_at).toLocaleString():''):'Email: '+esc(o.invoice.email_status||'Not sent')}</span></div>`:''}${cancel}${returns?`<div class="admin-returns"><h4>Return requests</h4>${returns}</div>`:''}</div>`;
}


async function showAdmin(box){
  const [stats,aps,orders,settings,mail,emailLogs,dbSummary,customers,dbTables,auditLogs]=await Promise.all([
    api('/api/admin/stats'),api('/api/admin/products'),api('/api/admin/orders'),api('/api/store-settings'),api('/api/admin/mail-settings'),api('/api/admin/email-logs'),api('/api/admin/database-summary'),api('/api/admin/customers'),api('/api/admin/database/tables'),api('/api/admin/audit-logs?limit=100')
  ]);
  const options=categories.map(c=>`<option>${esc(c)}</option>`).join('');
  const requests=[]; orders.forEach(o=>{if(o.cancellation)requests.push({type:'Cancellation',order:o.id,customer:o.customer_name,status:o.cancellation.status,reason:o.cancellation.reason,created_at:o.cancellation.created_at,attachments:o.cancellation.attachments||[]});(o.returns||[]).forEach(r=>requests.push({type:'Return',order:o.id,customer:o.customer_name,status:r.status,reason:r.reason,created_at:r.created_at,attachments:r.attachments||[],product:r.product_name,quantity:r.quantity,return_id:r.id}))}); requests.sort((a,b)=>String(b.created_at).localeCompare(String(a.created_at)));
  const payOrders=orders.map(o=>({o,p:o.payment||{}}));
  const payCounts={cod:payOrders.filter(x=>x.o.payment_method==='Cash on Delivery').length,upiNow:payOrders.filter(x=>x.o.payment_method==='UPI Payment').length,upiDelivery:payOrders.filter(x=>x.o.payment_method==='UPI on Delivery').length,pending:payOrders.filter(x=>['UPI Payment','UPI on Delivery'].includes(x.o.payment_method)&&x.p.payment_status==='Pending Verification').length};
  const paymentRows=payOrders.length?payOrders.map(({o,p})=>`<tr><td>#${o.id}</td><td>${esc(o.customer_name)}</td><td><b>${esc(o.payment_method)}</b></td><td>${money(p.current_amount??o.revised_total)}</td><td>${esc(o.upi_reference||'—')}</td><td>${paymentBadge(p)}</td><td>${['UPI Payment','UPI on Delivery'].includes(o.payment_method)&&p.payment_status==='Pending Verification'&&o.upi_reference?`<button class="mini strong" onclick="markPaymentReceived(${o.id},'payments')">Confirm Received</button>`:'—'}</td></tr>`).join(''):'<tr><td colspan="7" class="muted">No payments yet.</td></tr>';
  const adminRequestsHtml=requests.length?requests.map(r=>`<div class="admin-request-card"><div><span class="eyebrow">${esc(r.type.toUpperCase())} · ORDER #${r.order}</span><h4>${esc(r.customer)}</h4><small>${new Date(r.created_at).toLocaleString()}</small></div><span class="badge">${esc(r.status)}</span>${r.product?`<p><b>Product:</b> ${esc(r.product)} · Qty ${r.quantity}</p>`:''}<p><b>Reason:</b> ${esc(r.reason)}</p>${requestPhotos(r.attachments)}${r.type==='Return'&&r.status==='Pending'?`<div><button class="mini" onclick="resolveReturn(${r.return_id},'Approved')">Approve</button> <button class="mini danger" onclick="resolveReturn(${r.return_id},'Rejected')">Reject</button></div>`:''}</div>`).join(''):'<div class="empty">No returns or cancellations yet.</div>';
  box.innerHTML=`
  <div class="account-head"><div><span class="eyebrow">ADMIN DASHBOARD</span><h2>Maneendra General Stores</h2><p class="muted">Manage products, payments, customer orders, delivery OTP verification, cancellations, returns and email notifications.</p></div><button class="ghost" onclick="logout()">Logout</button></div>
  <div class="admin-stats dashboard-stats"><div class="stat"><small>Today's orders</small><b>${stats.today_orders}</b></div><div class="stat"><small>Today's sales</small><b>${money(stats.today_sales)}</b></div><div class="stat"><small>Pending UPI</small><b>${stats.pending_upi}</b></div><div class="stat"><small>Out for delivery</small><b>${stats.out_for_delivery}</b></div><div class="stat"><small>Pending returns</small><b>${stats.pending_returns}</b></div><div class="stat"><small>Low stock</small><b>${stats.low_stock}</b></div><div class="stat"><small>Out of stock</small><b>${stats.out_of_stock}</b></div><div class="stat"><small>All-time sales</small><b>${money(stats.revenue)}</b></div></div>
  <div class="admin-tabs"><button onclick="adminSection('products')">Products</button><button onclick="adminSection('customers')">Customers</button><button onclick="adminSection('orders')">Orders & Delivery</button><button onclick="adminSection('payments')">Payments</button><button onclick="adminSection('requests')">Returns & Cancellations</button><button onclick="adminSection('settings')">Store Details</button><button onclick="adminSection('mail')">Email Notifications</button><button onclick="adminSection('database')">Database</button><button onclick="adminSection('audit')">Audit & Backup</button></div>
  <section id="adminProducts"><div class="section-mini-head"><div><h3>Products & Inventory</h3><p class="muted">Stock is reserved when an order is placed. Set MRP, selling price, purchase price and reorder level for each pack size.</p></div><div class="image-sync-actions"><button class="ghost strong" onclick="syncAllRealImages()">Fetch / cache real photos</button><button class="ghost danger hidden" id="stopImageSync" onclick="stopRealImageSync()">Stop</button></div></div><div id="imageSyncProgress" class="sync-progress hidden"></div><h3>Add Product / Pack Size</h3><form id="adminProductForm" class="admin-product-form"><input name="name" placeholder="Product name" required><select name="category">${options}</select><input name="unit" placeholder="Pack size e.g. 1 kg" required><input name="mrp" type="number" step="0.01" placeholder="MRP"><input name="price" type="number" step="0.01" placeholder="Selling price" required><input name="purchase_price" type="number" step="0.01" placeholder="Purchase price"><input name="stock" type="number" placeholder="Stock" required><input name="reorder_level" type="number" value="5" placeholder="Low-stock level"><input name="sku" placeholder="SKU (optional)"><input name="barcode" placeholder="Barcode (optional)"><label class="file-field">Product image<input name="image_file" type="file" accept="image/png,image/jpeg,image/webp"></label><button class="primary">Add</button></form><p class="hint">For multiple pack sizes, add the same product name with different units (500 g, 1 kg, 5 kg). Each pack keeps its own MRP, selling price and stock.</p><div class="table-wrap"><table class="admin-table"><thead><tr><th>ID</th><th>Image</th><th>Product</th><th>Unit</th><th>MRP</th><th>Sell</th><th>Cost</th><th>Stock</th><th>Low at</th><th>Action</th></tr></thead><tbody>${aps.filter(p=>p.active).map(p=>`<tr class="${p.stock<=Number(p.reorder_level||5)?'low-stock-row':''}"><td>${p.id}</td><td><img class="admin-thumb" src="${esc(p.image)}" onerror="this.onerror=null;this.src='/static/images/products/default-product.png'"><input class="admin-file" id="img${p.id}" type="file" accept="image/png,image/jpeg,image/webp"></td><td><input id="n${p.id}" value="${esc(p.name)}"><select id="c${p.id}">${categories.map(c=>`<option ${c===p.category?'selected':''}>${esc(c)}</option>`).join('')}</select><input id="sku${p.id}" value="${esc(p.sku||'')}" placeholder="SKU"><input id="bar${p.id}" value="${esc(p.barcode||'')}" placeholder="Barcode"></td><td><input id="u${p.id}" value="${esc(p.unit)}"></td><td><input id="m${p.id}" type="number" step="0.01" value="${p.mrp||p.price}"></td><td><input id="p${p.id}" type="number" step="0.01" value="${p.price}"></td><td><input id="cost${p.id}" type="number" step="0.01" value="${p.purchase_price||0}"></td><td><input id="s${p.id}" type="number" value="${p.stock}"></td><td><input id="r${p.id}" type="number" value="${p.reorder_level??5}"></td><td><button class="mini" onclick="saveProduct(${p.id},'${String(p.image).replaceAll("'","\'")}')">Save</button> <button class="mini photo" onclick="syncOneRealImage(${p.id},true)">Real photo</button> <button class="mini danger" onclick="hideProduct(${p.id})">Hide</button></td></tr>`).join('')}</tbody></table></div></section>
  <section id="adminCustomers" class="hidden"><div class="section-mini-head"><div><h3>Customers</h3><p class="muted">Customer profiles are saved permanently in SQLite and update whenever customers edit their Profile.</p></div></div><div class="table-wrap"><table class="admin-table customer-table"><thead><tr><th>ID</th><th>Name</th><th>Email</th><th>Phone</th><th>Address</th><th>Orders</th><th>Order value</th><th>Updated</th></tr></thead><tbody>${customers.length?customers.map(c=>`<tr><td>${c.id}</td><td><b>${esc(c.full_name)}</b></td><td>${esc(c.email)}</td><td>${esc(c.phone||'—')}</td><td>${esc(c.address||'—')}</td><td>${c.order_count}</td><td>${money(c.order_value)}</td><td>${c.updated_at?new Date(c.updated_at).toLocaleString():'—'}</td></tr>`).join(''):'<tr><td colspan="8" class="muted">No customers registered yet.</td></tr>'}</tbody></table></div></section>
  <section id="adminOrders" class="hidden"><div class="section-mini-head"><div><h3>Orders & Delivery</h3><p class="muted">Reduce unavailable quantities, see revised totals, send Out-for-Delivery OTPs and verify delivery.</p></div></div>${orders.length?orders.map(adminOrderCard).join(''):'<div class="empty">No orders yet.</div>'}</section>
  <section id="adminPayments" class="hidden"><div class="section-mini-head"><div><h3>Payments</h3><p class="muted">COD, Pay Now UPI and UPI on Delivery are tracked separately. Confirm UPI only after checking the shop account.</p></div></div><div class="admin-stats payment-stats"><div class="stat"><small>COD</small><b>${payCounts.cod}</b></div><div class="stat"><small>UPI Pay Now</small><b>${payCounts.upiNow}</b></div><div class="stat"><small>UPI on Delivery</small><b>${payCounts.upiDelivery}</b></div><div class="stat"><small>UPI Pending</small><b>${payCounts.pending}</b></div></div><div class="table-wrap"><table class="admin-table"><thead><tr><th>Order</th><th>Customer</th><th>Method</th><th>Amount</th><th>UTR / Ref</th><th>Status</th><th>Action</th></tr></thead><tbody>${paymentRows}</tbody></table></div></section>
  <section id="adminRequests" class="hidden"><div class="section-mini-head"><div><h3>Returns & Cancellations</h3><p class="muted">See customer reasons and all attached photos stored with each request.</p></div></div><div class="admin-request-grid">${adminRequestsHtml}</div></section>
  <section id="adminSettings" class="hidden"><h3>Store Details</h3><form id="storeSettingsForm" class="form store-settings-form"><div class="two"><label>Mobile number<input name="phone" value="${esc(settings.phone)}" required></label><label>WhatsApp number<input name="whatsapp" value="${esc(settings.whatsapp)}" required></label></div><label>Email<input type="email" name="email" value="${esc(settings.email)}" required></label><label>Store address<textarea name="address" rows="3" required>${esc(settings.address)}</textarea></label><label>Google Maps link<input name="map_url" value="${esc(settings.map_url)}" required></label><label>Opening hours<input name="hours" value="${esc(settings.hours)}" required></label><div class="two"><label>UPI ID<input name="upi_id" value="${esc(settings.upi_id||'')}" placeholder="example@bank"></label><label>UPI payee name<input name="upi_payee_name" value="${esc(settings.upi_payee_name||'Maneendra General Stores')}" required></label></div><button class="primary">Save Store Details</button></form></section>
  <section id="adminMail" class="hidden"><div class="mail-settings-head"><div><h3>Email Notifications</h3><p class="muted">Order updates, delivery OTPs, payment confirmations, returns and final PDF bills are sent through the Brevo HTTPS API.</p></div><span class="mail-status ${mail.api_key_set&&mail.email_notifications_enabled?'ready':'setup'}">${mail.api_key_set&&mail.email_notifications_enabled?'Brevo configured':'API key required'}</span></div>
    <form id="mailSettingsForm" class="form mail-settings-form"><label class="switch-row"><span><b>Enable automatic customer emails</b><small>Order updates, OTPs, revised totals, payment confirmations, returns and delivered-order PDF bills.</small></span><input id="emailNotificationsEnabled" type="checkbox" ${mail.email_notifications_enabled?'checked':''}></label><label>Verified Brevo sender email<input type="email" name="sender_email" value="${esc(mail.sender_email||settings.email)}" required></label><label>Sender display name<input name="sender_name" value="${esc(mail.sender_name||'Maneendra General Stores')}" required></label><p class="hint"><b>Brevo API:</b> keep the API key out of the database. Set <code>BREVO_API_KEY</code> in your local <code>.env</code> or Railway Variables. The sender email must be verified in Brevo.</p><button class="primary">Save Email Settings</button></form>
    <div class="test-mail-box"><h4>Send Test Email</h4><form id="testEmailForm" class="test-email-form"><input type="email" name="to_email" value="${esc(mail.sender_email||settings.email)}" placeholder="Email to test" required><button class="ghost strong">Send Test</button></form></div>
    <h4>Recent Email Log</h4><div class="table-wrap"><table class="admin-table mail-log-table"><thead><tr><th>Time</th><th>Order</th><th>To</th><th>Status</th><th>Details</th></tr></thead><tbody>${emailLogs.length?emailLogs.map(l=>`<tr><td>${new Date(l.created_at).toLocaleString()}</td><td>${l.order_id?'#'+l.order_id:'—'}</td><td>${esc(l.to_email)}</td><td><span class="mail-result ${String(l.status).toLowerCase()}">${esc(l.status)}</span></td><td><small>${esc(l.error||l.subject)}</small></td></tr>`).join(''):'<tr><td colspan="5" class="muted">No email attempts yet.</td></tr>'}</tbody></table></div>
  </section>
  <section id="adminDatabase" class="hidden"><div class="section-mini-head"><div><h3>Database Explorer</h3><p class="muted">Click any SQLite table to see its columns and saved records. This viewer is read-only and masks password / OTP hashes.</p></div></div><div class="db-grid">${dbTables.tables.map(t=>`<button class="db-card db-card-button" onclick="openDbTable('${esc(t.name)}',0)"><small>${esc(t.name)}</small><b>${t.row_count}</b><span>${t.columns.length} columns · View records →</span></button>`).join('')}</div><div id="dbDetail" class="db-detail"><p class="hint"><b>Database file:</b> ${esc(dbTables.database_file||'store.db')}. Click a table above to inspect its schema and rows.</p></div></section>
  <section id="adminAudit" class="hidden"><div class="section-mini-head"><div><h3>Audit & Backup</h3><p class="muted">Download a copy of store.db and review important actions.</p></div><a class="primary backup-link" href="/api/admin/backup">Download Database Backup</a></div><div class="hint">Keep backups outside this project folder. Delivery remains <b>FREE</b>; no delivery-charge setting is used.</div><div class="table-wrap"><table class="admin-table"><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Entity</th><th>Details</th></tr></thead><tbody>${auditLogs.length?auditLogs.map(a=>`<tr><td>${new Date(a.created_at).toLocaleString()}</td><td>${esc(a.actor_name||a.actor_email||'System')}</td><td><b>${esc(a.action)}</b></td><td>${esc(a.entity_type)} ${esc(a.entity_id||'')}</td><td>${esc(a.details||'')}</td></tr>`).join(''):'<tr><td colspan="5" class="muted">No audit activity yet.</td></tr>'}</tbody></table></div></section>`;
  $('#adminProductForm').onsubmit=addAdminProduct;
  $('#storeSettingsForm').onsubmit=saveStoreSettings;
  $('#mailSettingsForm').onsubmit=saveMailSettings;
  $('#testEmailForm').onsubmit=sendTestEmail;
}

function adminSection(which){
  $('#adminProducts').classList.toggle('hidden',which!=='products');
  $('#adminCustomers').classList.toggle('hidden',which!=='customers');
  $('#adminOrders').classList.toggle('hidden',which!=='orders');
  $('#adminPayments').classList.toggle('hidden',which!=='payments');
  $('#adminRequests').classList.toggle('hidden',which!=='requests');
  $('#adminSettings').classList.toggle('hidden',which!=='settings');
  $('#adminMail').classList.toggle('hidden',which!=='mail');
  $('#adminDatabase').classList.toggle('hidden',which!=='database');
  $('#adminAudit').classList.toggle('hidden',which!=='audit');
}
function dbCell(v){
  if(v===null||v===undefined)return '<span class="muted">NULL</span>';
  const text=String(v);
  return `<span title="${esc(text)}">${esc(text.length>120?text.slice(0,117)+'…':text)}</span>`;
}
async function openDbTable(name,offset=0){
  const box=$('#dbDetail'); if(!box)return;
  box.innerHTML='<div class="db-loading">Loading table…</div>';
  try{
    const d=await api(`/api/admin/database/table/${encodeURIComponent(name)}?limit=50&offset=${offset}`);
    const cols=d.columns.map(c=>c.name);
    const schema=d.columns.map(c=>`<tr><td><b>${esc(c.name)}</b></td><td>${esc(c.type||'')}</td><td>${c.pk?'Yes':'No'}</td><td>${c.notnull?'No':'Yes'}</td><td>${dbCell(c.dflt_value)}</td></tr>`).join('');
    const rows=d.rows.length?d.rows.map(r=>`<tr>${cols.map(c=>`<td>${dbCell(r[c])}</td>`).join('')}</tr>`).join(''):`<tr><td colspan="${Math.max(1,cols.length)}" class="muted">No records in this table.</td></tr>`;
    const start=d.total?d.offset+1:0,end=Math.min(d.total,d.offset+d.limit),prev=Math.max(0,d.offset-d.limit),next=d.offset+d.limit;
    box.innerHTML=`<div class="db-detail-head"><div><span class="eyebrow">SQLITE TABLE</span><h3>${esc(d.table)}</h3><p class="muted">${d.total} records · showing ${start}-${end}</p></div><button class="ghost" onclick="closeDbTable()">Close details</button></div><details class="db-schema"><summary>Table columns / schema</summary><div class="table-wrap"><table class="admin-table"><thead><tr><th>Column</th><th>Type</th><th>Primary key</th><th>Nullable</th><th>Default</th></tr></thead><tbody>${schema}</tbody></table></div></details><h4>Saved records</h4><div class="table-wrap db-record-wrap"><table class="admin-table db-record-table"><thead><tr>${cols.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div><div class="db-pager"><button class="ghost" ${d.offset<=0?'disabled':''} onclick="openDbTable('${esc(d.table)}',${prev})">← Previous</button><span>${start}-${end} of ${d.total}</span><button class="ghost" ${next>=d.total?'disabled':''} onclick="openDbTable('${esc(d.table)}',${next})">Next →</button></div>`;
  }catch(err){box.innerHTML=`<div class="empty">${esc(err.message)}</div>`}
}
function closeDbTable(){const box=$('#dbDetail');if(box)box.innerHTML='<p class="hint">Click a table card above to inspect its columns and records.</p>'}

async function syncOneRealImage(id,force=false,silent=false){
  try{
    const d=await api(`/api/admin/products/${id}/sync-real-image?force_search=${force?'true':'false'}`,{method:'POST'});
    if(!silent){
      if(d.status==='updated')toast(`Real photo saved for ${d.name}`);
      else toast(`${d.name}: ${d.message||d.status}`);
      await loadProducts(); await showAdmin($('#accountContent')); adminSection('products');
    }
    return d;
  }catch(err){if(!silent)toast(err.message);return {status:'failed',message:err.message,product_id:id}}
}
function stopRealImageSync(){imageSyncCancelled=true;toast('Stopping after the current product…')}
async function syncAllRealImages(){
  const box=$('#imageSyncProgress'),stop=$('#stopImageSync'); if(!box)return;
  imageSyncCancelled=false; stop?.classList.remove('hidden'); box.classList.remove('hidden');
  let list=[]; try{list=await api('/api/admin/products')}catch(err){toast(err.message);return}
  list=list.filter(p=>p.active);
  let updated=0,failed=0,notFound=0,skipped=0;
  for(let i=0;i<list.length;i++){
    if(imageSyncCancelled)break;
    const p=list[i];
    box.innerHTML=`<b>Fetching real product photos…</b><span>${i+1} / ${list.length}: ${esc(p.name)}</span><small>Saved locally after a web match is found. This can take several minutes.</small>`;
    // Existing remote branded images are cached directly. Generic local placeholders are searched by product name.
    const d=await syncOneRealImage(p.id,false,true);
    if(d.status==='updated')updated++; else if(d.status==='not_found')notFound++; else if(d.status==='already_local')skipped++; else failed++;
    await new Promise(r=>setTimeout(r,350));
  }
  stop?.classList.add('hidden');
  box.innerHTML=`<b>Real-photo sync finished${imageSyncCancelled?' (stopped)':''}</b><span>Updated ${updated} · no match ${notFound} · failed ${failed} · skipped ${skipped}</span><small>You can use the “Real photo” button on any product to force a fresh search, or upload an exact photo manually.</small>`;
  await loadProducts(); await showAdmin($('#accountContent')); adminSection('products');
}

async function saveStoreSettings(e){e.preventDefault();const data=Object.fromEntries(new FormData(e.target));try{await api('/api/admin/store-settings',{method:'PUT',body:JSON.stringify(data)});storeSettings=data;renderStoreDetails();toast('Store details updated')}catch(err){toast(err.message)}}
async function saveMailSettings(e){e.preventDefault();const f=Object.fromEntries(new FormData(e.target));const data={sender_email:f.sender_email,sender_name:f.sender_name,email_notifications_enabled:$('#emailNotificationsEnabled').checked};try{await api('/api/admin/mail-settings',{method:'PUT',body:JSON.stringify(data)});toast('Brevo email settings saved');await showAdmin($('#accountContent'));adminSection('mail')}catch(err){toast(err.message)}}
async function sendTestEmail(e){e.preventDefault();const data=Object.fromEntries(new FormData(e.target));try{await api('/api/admin/test-email',{method:'POST',body:JSON.stringify(data)});toast('Test email sent successfully');await showAdmin($('#accountContent'));adminSection('mail')}catch(err){toast(err.message)}}
async function uploadImage(file){if(!file||!file.size)return null;const fd=new FormData();fd.append('file',file);const r=await fetch('/api/admin/upload-image',{method:'POST',body:fd});let d={};try{d=await r.json()}catch{}if(!r.ok)throw new Error(d.detail||'Image upload failed');return d.url}
async function addAdminProduct(e){e.preventDefault();const form=e.target;const fd=new FormData(form);const file=fd.get('image_file');const f=Object.fromEntries(fd);delete f.image_file;['price','stock','mrp','purchase_price','reorder_level'].forEach(k=>f[k]=Number(f[k]||0));try{f.image=(file&&file.size)?await uploadImage(file):'/static/images/products/default-product.png';f.description=f.name+' - store item';f.active=true;await api('/api/admin/products',{method:'POST',body:JSON.stringify(f)});toast('Product added');categories=await api('/api/categories');renderCategories();await loadProducts();await showAdmin($('#accountContent'))}catch(err){toast(err.message)}}
async function saveProduct(id,image){try{const file=$('#img'+id)?.files?.[0];if(file)image=await uploadImage(file);const data={name:$('#n'+id).value,category:$('#c'+id).value,unit:$('#u'+id).value,mrp:+$('#m'+id).value,price:+$('#p'+id).value,purchase_price:+$('#cost'+id).value,stock:+$('#s'+id).value,reorder_level:+$('#r'+id).value,sku:$('#sku'+id).value,barcode:$('#bar'+id).value,image:image||'/static/images/products/default-product.png',description:$('#n'+id).value+' - store item',active:true};await api('/api/admin/products/'+id,{method:'PUT',body:JSON.stringify(data)});toast('Product updated');await loadProducts();await showAdmin($('#accountContent'))}catch(err){toast(err.message)}}
async function hideProduct(id){if(!confirm('Hide this product from the store?'))return;try{await api('/api/admin/products/'+id,{method:'DELETE'});toast('Product hidden');await loadProducts();await showAdmin($('#accountContent'))}catch(err){toast(err.message)}}
async function reloadAdminOrders(){await showAdmin($('#accountContent'));adminSection('orders')}
async function setOrderStatus(id,status){try{const d=await api('/api/admin/orders/'+id+'/status?status='+encodeURIComponent(status),{method:'PATCH'});toast(d.otp_email?'Out for Delivery set — OTP emailed to customer':(d.email_notification==='queued'?'Status updated; customer email queued':d.message));await reloadAdminOrders()}catch(err){toast(err.message);await reloadAdminOrders()}}
async function markPaymentReceived(id,returnTab='orders'){if(!confirm(`Confirm that UPI payment for order #${id} has been received?`))return;try{await api(`/api/admin/orders/${id}/payment`,{method:'PATCH',body:JSON.stringify({status:'Payment Confirmed',note:'UPI payment checked by admin and received'})});toast('Payment confirmed — customer email queued');await showAdmin($('#accountContent'));adminSection(returnTab)}catch(err){toast(err.message)}}
async function saveOrderItems(id){const inputs=$$(`[data-order="${id}"][data-order-item]`);const items=[...inputs].map(x=>({order_item_id:+x.dataset.orderItem,quantity:+x.value}));if(!confirm('Save these revised quantities? Removed quantities will be returned to stock.'))return;try{const d=await api(`/api/admin/orders/${id}/items`,{method:'PATCH',body:JSON.stringify({items})});toast(`Order updated. Revised total ${money(d.financials.revised_total)}`);await loadProducts();await reloadAdminOrders()}catch(err){toast(err.message)}}
async function verifyDelivery(id){const otp=$(`#deliveryOtp${id}`)?.value?.trim();if(!otp){toast('Enter the customer delivery OTP');return}try{await api(`/api/admin/orders/${id}/verify-delivery`,{method:'POST',body:JSON.stringify({otp})});toast('OTP verified — delivered. Final PDF bill email queued.');await reloadAdminOrders()}catch(err){toast(err.message)}}
async function resendInvoice(id){if(!confirm(`Resend the final PDF bill for order #${id} to the customer?`))return;try{await api(`/api/admin/orders/${id}/resend-invoice`,{method:'POST'});toast('Final bill email queued');await reloadAdminOrders()}catch(err){toast(err.message)}}
async function resolveReturn(id,status){if(!confirm(`${status} this return request?`))return;try{const d=await api(`/api/admin/returns/${id}`,{method:'PATCH',body:JSON.stringify({status})});toast(d.message+(d.refund_amount?` · Refund ${money(d.refund_amount)}`:''));await loadProducts();await reloadAdminOrders()}catch(err){toast(err.message)}}
async function markReturnRefunded(id){if(!confirm('Mark this return refund as completed?'))return;try{await api(`/api/admin/returns/${id}/refund-status`,{method:'PATCH',body:JSON.stringify({status:'Refunded',note:'Refund marked completed by admin'})});toast('Refund marked as completed');await reloadAdminOrders()}catch(err){toast(err.message)}}

window.addToCart=addToCart; window.changeQty=changeQty; window.removeCart=removeCart; window.logout=logout;
window.customerSection=customerSection; window.openCancelRequest=openCancelRequest; window.openReturnRequest=openReturnRequest; window.closeRequestModal=closeRequestModal; window.editSavedAddress=editSavedAddress; window.makeDefaultAddress=makeDefaultAddress; window.deleteSavedAddress=deleteSavedAddress; window.buyAgain=buyAgain;
window.adminSection=adminSection; window.markPaymentReceived=markPaymentReceived; window.resendInvoice=resendInvoice; window.markReturnRefunded=markReturnRefunded; window.saveProduct=saveProduct; window.hideProduct=hideProduct; window.setOrderStatus=setOrderStatus; window.saveOrderItems=saveOrderItems; window.verifyDelivery=verifyDelivery; window.resolveReturn=resolveReturn; window.openDbTable=openDbTable; window.closeDbTable=closeDbTable; window.syncOneRealImage=syncOneRealImage; window.syncAllRealImages=syncAllRealImages; window.stopRealImageSync=stopRealImageSync;
init();
