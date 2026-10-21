### Challenge 2: Matching Order Headers with Order Lines

#### Problem Statement

Imagine a procurement department comes to us. With the following. Due to some error, they lost the connection between order lines and order headers and now ask us to reconnect headers and lines.

You are given three CSV files:

1. **Order Headers**: This file contains order headers with the total price and the expected number of order lines.
2. **Order Lines**: This file contains order lines without an explicit connection to the order headers.

Your task is to find and match each order header with its corresponding order lines such that the sum of the prices of the order lines equals the `total_price` of the order header, and the number of matched order lines equals `total_lines`. Each order header has between 1 and 10 order lines.

### Input

- **Order Headers CSV**: Contains the following columns:
  - `id`: A unique identifier for the order.
  - `total_price`: The total price of the order.
  - `total_lines`: The number of order lines expected for this order.

- **Order Lines CSV**: Contains the following columns:
  - `description`: A description of the item.
  - `price`: The price of the item.

### Output

- A list of matched orders. Each matched order should include:
  - `id`: The unique identifier of the order header.
  - `total_price`: The total price of the order header.
  - `total_lines`: The number of order lines expected for the order header.
  - `order_lines`: A list of order line objects that sum up to the `total_price` of the order header.

### Requirements

1. Ensure that each order header is matched with a combination of order lines whose prices sum up to the `total_price` of the order header.
2. Ensure that the number of matched order lines equals `total_lines`.
3. Only valid matches should be included in the output.

### Example

**Order Headers CSV:**
```csv
id,total_price,total_lines
1,100,2
2,200,2
```

**Order Lines CSV:**
```csv
description,price
Item A,50.00
Item B,50.00
Item C,150.00
Item D,50.00
```

**Output:**
```json
[
    {
        "id": 1,
        "total_price": 100,
        "total_lines": 2,
        "order_lines": [
            {"description": "Item A", "price": 50.00},
            {"description": "Item B", "price": 50.00}
        ]
    },
    {
        "id": 2,
        "total_price": 200,
        "total_lines": 2,
        "order_lines": [
            {"description": "Item C", "price": 150.00},
            {"description": "Item D", "price": 50.00}
        ]
    }
]
```

### Notes

- Assume all prices are positive integers or decimals with two decimal places.
- Order lines are limited to 10 per order header.
- There might be multiple valid combinations of order lines for a given order header.
- The focus should be on ensuring the accuracy of the matches and that the number of order lines matches the `total_lines` specified in the header.
