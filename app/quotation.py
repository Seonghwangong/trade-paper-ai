from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/quotation-list")
def quotation_list():

    html = """
<h1>Quotation List</h1>

<p><a href="/quotation-form">+ New Quotation</a></p>

<table border="1" style="border-collapse:collapse;width:100%;">
<tr>
    <th>Quotation No</th>
    <th>Buyer</th>
    <th>Date</th>
    <th>Total</th>
    <th>PDF</th>
    <th>Edit</th>
    <th>Delete</th>
</tr>

<tr>
    <td colspan="7" align="center">
        No Quotations
    </td>
</tr>

</table>

<br>
<a href="/">Back Home</a>
"""

    return HTMLResponse(html)

@router.get("/quotation-form")
def quotation_form():

    html = """
<h1>Quotation Input</h1>

<p>Buyer</p>
<select id="buyer">
    <option value="">Select Buyer</option>
</select>

<br><br>
<input id="buyer_name" type="text" placeholder="Buyer Name">

<br><br>
<input id="buyer_address" type="text" placeholder="Address">

<br><br>
<input id="buyer_email" type="text" placeholder="Email">

<p>Seller</p>
<input id="seller" type="text">

<p>Currency</p>
<select>
    <option>USD</option>
    <option>EUR</option>
    <option>KRW</option>
</select>

<p>Items</p>

<table border="1" style="border-collapse:collapse;width:100%;">
<tr>
    <th>Item</th>
    <th>Qty</th>
    <th>Unit Price</th>
    <th>Amount</th>
</tr>
<tr>
    <td><input type="text"></td>
    <td><input type="text"></td>
    <td><input type="text"></td>
    <td><input type="text"></td>
</tr>
</table>

<br>
<button>Save Quotation</button>

<br><br>
<a href="/quotation-list">Back to List</a>

<script>
let buyers = [];

async function loadBuyers() {
    const response = await fetch("/buyer-data");
    buyers = await response.json();

    const select = document.getElementById("buyer");

    buyers.forEach((buyer, index) => {
        select.innerHTML += `<option value="${index}">${buyer.name}</option>`;
    });
}

document.getElementById("buyer").addEventListener("change", function () {
    if (this.value === "") return;

    const buyer = buyers[this.value];

    document.getElementById("buyer_name").value = buyer.name || "";
    document.getElementById("buyer_address").value = buyer.address || "";
    document.getElementById("buyer_email").value = buyer.email || "";
});

loadBuyers();
</script>
"""

    return HTMLResponse(html)