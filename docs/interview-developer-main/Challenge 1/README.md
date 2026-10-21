# Challenge 1

Please organize, design, test, and deploy your code (locally on your machine is fine) as if it were going into production. Then send us a link to the hosted repository (e.g. Github, Bitbucket...).

## Challenge Description

*The askLio Team has identified the need to create and organize new requests for procurement. If users want to buy a product or service they need to create a formal request to the procurement department. That will afterwards process this request*

### 1. Intake
The initial step in submitting a procurement request is the intake process. This phase is crucial as it gathers all necessary details for the procurement department to proceed. Below is a table of the required information:


| Field Name                       | Description                                                        | Example                               |
|----------------------------------|--------------------------------------------------------------------|---------------------------------------|
| Requestor Name                   | Full name of the person submitting the request.                    | John Doe                              |
| Title/Short Description          | Brief name or description of the product/service requested.        | Adobe Creative Cloud Subscription     |
| Vendor Name                      | Name of the company or individual providing the items/services.    | Adobe Systems                         |
| Umsatzsteuer-Identifikationsnummer (VAT ID) | VAT identification number of the vendor. | DE123456789                            |
| Commodity Group                  | The category or group the requested items/services belong to.      | Software Licenses                     |
| Order Lines                      | List of positions from the offer, detailed as follows:             |                                       |
|                                  | - Position Description: Description of the item/service.           | Adobe Photoshop License               |
|                                  | - Unit Price: Price per unit/item/service.                         | 200                                   |
|                                  | - Amount: The quantity or number of units being ordered.           | 5                                     |
|                                  | - Unit: The unit of measure or quantity (e.g., licenses).          | licenses                              |
|                                  | - Total Price: Total price for this line (Unit Price x Amount).    | 1000                                  |
| Total Cost                       | Estimated total cost of the request.                               | 3000                                  |
| Department                        | The Deparment of the Requestor                   | HR                            |


In the first step build the functionality to create and submit new requests.

Feedback from user interviews has highlighted that requestors often have a vendor's offer at hand. To streamline the process, your task is to introduce an automatic extraction feature, allowing users to upload a document, which then auto-fills the request.

*Vendor Offer*
```
Vendor Name: Global Tech Solutions
Umsatzsteuer-Identifikationsnummer (VAT ID): DE987654321
Offer Date: March 23, 2024

Offered to: Creative Marketing Department

Items Offered:
1. Product: Adobe Photoshop License
   Unit Price: €150
   Quantity: 10
   Total: €1500

2. Product: Adobe Illustrator License
   Unit Price: €120
   Quantity: 5
   Total: €600

Total Offer Cost: €2100

Terms and Conditions:
Payment due within 30 days of invoice. Prices include applicable taxes.

```

*Extracted Information*
```

- Vendor Name: Global Tech Solutions
- Umsatzsteuer-Identifikationsnummer (VAT ID): DE987654321
- Requestor Department: Creative Marketing Department
- Order Lines:
  - Item 1:
    - Product: Adobe Photoshop License
    - Unit Price: €150
    - Quantity: 10
    - Total: €1500
  - Item 2:
    - Product: Adobe Illustrator License
    - Unit Price: €120
    - Quantity: 5
    - Total: €600
- Total Cost: €2100

```

Additionally, choosing the correct commodity group has been a challenge for users due to its complexity. A procurement manager of the customer says to you: "I don't want that the users have to select the commodity group, they always pick the wrong one, isn't there a better solution?"

**Key Points:**
- Ensure every request is saved for future reference.
- Aim to simplify the request submission process for users.
- Ensure that only valid requests can be submitted.



### 2. Request Overview
With the capability to submit requests, the procurement department requires a system to view and manage these submissions. It's critical to track and update the status of each request (Open, In Progress, Closed) to maintain transparency within the department.

**Key Points:**
- Implement a system to retain every status update accurately.


## Requirements
- Build a program with an web-based front-end to fulfill the above requirenments of the customer
- Choose the tech stack of your choice
- It should be a usuable product
- Deployment on your local machine is fine

## Some Final Remarks
- We actively encourage our employees to use ChatGPT and GitHub Copilot 😉
- Also feel free to use libraries like (LangChain, etc.) that allow you to build faster.
- We will provide you an OpenAI api key
- There are many ways to convince us, but most important is that the key parts are working. Evaluation will also be subject to your background (e.g. if you are no front-end developer, a misplaced button is totally fine, as long as the product is usable) 


## Additional Information
#### Commodity Groups

| ID  | Category                | Commodity Group                  |
|-----|-------------------------|-----------------------------------|
| 001 | General Services        | Accommodation Rentals            |
| 002 | General Services        | Membership Fees                  |
| 003 | General Services        | Workplace Safety                 |
| 004 | General Services        | Consulting                       |
| 005 | General Services        | Financial Services               |
| 006 | General Services        | Fleet Management                 |
| 007 | General Services        | Recruitment Services             |
| 008 | General Services        | Professional Development         |
| 009 | General Services        | Miscellaneous Services           |
| 010 | General Services        | Insurance                        |
| 011 | Facility Management     | Electrical Engineering           |
| 012 | Facility Management     | Facility Management Services     |
| 013 | Facility Management     | Security                         |
| 014 | Facility Management     | Renovations                      |
| 015 | Facility Management     | Office Equipment                 |
| 016 | Facility Management     | Energy Management                |
| 017 | Facility Management     | Maintenance                      |
| 018 | Facility Management     | Cafeteria and Kitchenettes       |
| 019 | Facility Management     | Cleaning                         |
| 020 | Publishing Production   | Audio and Visual Production      |
| 021 | Publishing Production   | Books/Videos/CDs                 |
| 022 | Publishing Production   | Printing Costs                   |
| 023 | Publishing Production   | Software Development for Publishing |
| 024 | Publishing Production   | Material Costs                   |
| 025 | Publishing Production   | Shipping for Production          |
| 026 | Publishing Production   | Digital Product Development      |
| 027 | Publishing Production   | Pre-production                   |
| 028 | Publishing Production   | Post-production Costs            |
| 029 | Information Technology  | Hardware                         |
| 030 | Information Technology  | IT Services                      |
| 031 | Information Technology  | Software                         |
| 032 | Logistics               | Courier, Express, and Postal Services |
| 033 | Logistics               | Warehousing and Material Handling |
| 034 | Logistics               | Transportation Logistics         |
| 035 | Logistics               | Delivery Services                |
| 036 | Marketing & Advertising | Advertising                      |
| 037 | Marketing & Advertising | Outdoor Advertising              |
| 038 | Marketing & Advertising | Marketing Agencies               |
| 039 | Marketing & Advertising | Direct Mail                      |
| 040 | Marketing & Advertising | Customer Communication           |
| 041 | Marketing & Advertising | Online Marketing                 |
| 042 | Marketing & Advertising | Events                           |
| 043 | Marketing & Advertising | Promotional Materials            |
| 044 | Production              | Warehouse and Operational Equipment |
| 045 | Production              | Production Machinery             |
| 046 | Production              | Spare Parts                      |
| 047 | Production              | Internal Transportation          |
| 048 | Production              | Production Materials             |
| 049 | Production              | Consumables                      |
| 050 | Production              | Maintenance and Repairs          |




