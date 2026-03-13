
const baseAmount = document.getElementById('base-amount');
const targetAmount = document.getElementById('target-amount');
const baseCurrency = document.getElementById('base-currency');
const targetCurrency = document.getElementById('target-currency');
const rateText = document.getElementById('exchange-rate-text');

async function convertCurrency() {
    const from = baseCurrency.value;
    const to = targetCurrency.value;
    const amount = baseAmount.value;

    // ADD THIS LINE: Stop if the amount is empty or 0
    if (!amount || amount <= 0) {
        targetAmount.value = "";
        return;
    }

    try {
        const response = await fetch(`https://api.frankfurter.app/latest?amount=${amount}&from=${from}&to=${to}`);
        const data = await response.json();
        
        const convertedValue = data.rates[to];
        targetAmount.value = convertedValue.toFixed(2);
        
        // Update the small rate info text
        const singleRate = (convertedValue / amount).toFixed(4);
        rateText.innerText = `1 ${from} = ${singleRate} ${to}`;
    } catch (error) {
        console.error("Error fetching exchange rate:", error);
    }
}

// Listen for changes
[baseAmount, baseCurrency, targetCurrency].forEach(element => {
    element.addEventListener('input', convertCurrency);
});

// Initial load
convertCurrency();
