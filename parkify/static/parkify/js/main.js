function toggleForm() {

    const loginForm = document.getElementById("loginForm");
    const signupForm = document.getElementById("signupForm");
    const title = document.getElementById("title");
    const desc = document.getElementById("desc");
    const button = document.getElementById("toggleButton");

    if (signupForm.classList.contains("hidden")) {

        signupForm.classList.remove("hidden");
        loginForm.classList.add("hidden");

        title.innerText = "Join Parkify";
        desc.innerText = "Create an account and start booking parking spaces instantly.";
        button.innerText = "Already Have Account? Login";

    } else {

        signupForm.classList.add("hidden");
        loginForm.classList.remove("hidden");

        title.innerText = "Welcome to Parkify";
        desc.innerText = "Find and reserve parking spaces easily.";
        button.innerText = "Create New Account";
    }
}

// Role Selection
function selectRole(role, formType) {

    const hiddenInput = document.getElementById(formType + "_role");

    if (hiddenInput) {
        hiddenInput.value = role;
    }

    const cards = document.querySelectorAll("." + formType + "-role-card");

    cards.forEach(card => {
        card.classList.remove("active-role");
    });

    document.getElementById(formType + "_" + role)
        .classList.add("active-role");
}

// Forgot Password Modal
function openForgotModal() {
    document.getElementById("forgotModal").classList.remove("hidden");
}

function closeForgotModal() {
    document.getElementById("forgotModal").classList.add("hidden");
}