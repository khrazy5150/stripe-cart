const basket = get_basket();
const order_no = basket.order_number;


// document.querySelector( document ).ready ( function ()
// {
//     console.log ( 'ready!' );
//     document.querySelector('[data-toggle="tooltip"]').tooltip();
//     var template = document.querySelector( '#template' ).html ();
//     Mustache.parse ( template );
//     var rendered = Mustache.render ( template, get_basket () );
//     document.querySelector( '#template' ).html ( rendered );
//     if ( document.querySelector('.basket-body').hasScrollBar () )
//     {
//         document.querySelector('.column-titles').addClass('fix-overflow');
//         document.querySelector('.basket-body').niceScroll({autohidemode: false,cursorcolor:"#ffffff",cursorborder:"1px solid #D0D0D0",cursorborderradius: "0",background: "#ffffff"});
//     }
//     else
//     {
//         document.querySelector('.column-titles').removeClass('fix-overflow');
//     }
    
//     document.querySelector('.card-expiration').datepicker({
//     format: "mm/yyyy",
//     startView: "months", 
//     minViewMode: "months"        
// });
// });

function renderProducts() {
    let product_row = '';
    basket.products.forEach(product => {
        let one_row = `
        <div class="col-2 product-image"><img src="${product.thumbnail}"></div>
        <div class="col-5">${product.name}<br><span class="additional">${product.additional}</span></div>
        <div class="col-2 align-right"><span class="sub">${product.unit}</span> ${product.quantity}</div>
        <div class="col-3 align-right"><span class="sub">${product.currency}</span> ${product.price}</div>`;

        product_row += one_row;
    });
    document.getElementById('product').innerHTML = product_row;
}

function get_basket ()
{
    var products =
    [ 
        { name: "Product 1 lorem", additional: "Additional Informations", quantity: 1, unit: "pc", price: 10, thumbnail: "https://images.juniorbay.com/photos/0CqHGmpUmdE/thumb.jpg" }, 
        { name: "Product 2 ipsum", additional: "Additional Informations", quantity: 1, unit: "kg", price: 20, thumbnail: "http://via.placeholder.com/640x480" }, 
        { name: "Product 3 dolor sit amet", additional: "Additional Informations", quantity: 2, unit: "l", price: 30, thumbnail: "http://via.placeholder.com/1920x1080" },
        // { name: "Product 4 consectetur adipiscing elit", additional: "Additional Informations", quantity: 1, unit: "pcs", price: 25, thumbnail: "http://via.placeholder.com/800x400" },
        // { name: "Product 5 sed dapibus nibh", additional: "Additional Informations", quantity: 3, unit: "pcs", price: 9, thumbnail: "http://via.placeholder.com/400x800" },
        // { name: "Product 6 sit amet maximus ultrices", additional: "Additional Informations", quantity: 1, unit: "pcs", price: 13, thumbnail: "http://via.placeholder.com/2048x1024" },
        // { name: "Product 7 duis rutrum", additional: "Additional Informations", quantity: 5, unit: "pcs", price: 200, thumbnail: "http://via.placeholder.com/20x20" },
        // { name: "Product 8 efficitur lectus et facilisis", additional: "Additional Informations", quantity: 1, unit: "pc", price: 350, thumbnail: "http://via.placeholder.com/256x64" },
        // { name: "Product 9 nulla at ipsum nec risus vestibulum ullamcorper", additional: "Additional Informations", quantity: 10, unit: "pcs", price: 70, thumbnail: "http://via.placeholder.com/64x256" },
        // { name: "Product 10 proin facilisis magna", additional: "Additional Informations", quantity: 4, unit: "pcs", price: 1000, thumbnail: "http://via.placeholder.com/1024x768" },
        // { name: "Product 11 donec at arcu a tortor pellentesque cursus vel a quam", additional: "Additional Informations", quantity: 300, unit: "pcs", price: 6600, thumbnail: "http://via.placeholder.com/400x100" },
        // { name: "Product 12 nulla auctor libero in velit vulputate", additional: "Additional Informations", quantity: 6, unit: "pcs", price: 17.5, thumbnail: "http://via.placeholder.com/100x500" }
    ]
    return { "products": products, "order_number": "1-23-456789A", "subtotal": 13579, "taxes": 246, "shipping_cost": 810, "total": 16825, "currency": "&dollar;" };
}

document.addEventListener('DOMContentLoaded', async() => {
    document.getElementById('order-number').innerHTML = order_no;
    renderProducts();
})